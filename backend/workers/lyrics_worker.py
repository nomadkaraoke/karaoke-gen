"""
Lyrics transcription and correction worker.

Handles the lyrics processing track of parallel processing:
1. Fetch reference lyrics from multiple sources (Genius, Spotify, Musixmatch, LRCLib)
2. Transcribe audio with AudioShake API (1-2 min)
3. Run automatic correction using LyricsTranscriber
4. Generate corrections JSON for human review
5. Upload all data to GCS
6. Transition to AWAITING_REVIEW state

Re-uses:
- karaoke_gen.lyrics_processor.LyricsProcessor for lyrics fetching and orchestration
- lyrics_transcriber library (submodule) for transcription and correction

Observability:
- All operations wrapped in tracing spans for Cloud Trace visibility
- Logs include [job:ID] prefix for easy filtering in Cloud Logging
- Worker start/end timing logged with WORKER_START/WORKER_END markers
"""
import asyncio
import logging
import os
import shutil
import tempfile
import time
import json
from typing import Optional, Dict, Any
from pathlib import Path

from backend.models.job import JobStatus
from karaoke_gen.utils import sanitize_filename
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.lyrics_cache_service import LyricsCacheService
from backend.workers.worker_logging import create_job_logger, setup_job_logging, job_logging_context
from backend.workers.style_helper import load_style_config
from backend.workers.registry import worker_registry
from backend.services.tracing import job_span, add_span_event, add_span_attribute
from backend.services.metrics import metrics

# Import from karaoke_gen package
from karaoke_gen.lyrics_processor import LyricsProcessor
from backend.config import get_settings


logger = logging.getLogger(__name__)

# Timeout for entire transcription process including agentic AI correction (20 minutes)
# This needs to account for:
# - Cloud Run cold start / worker initialization (1-5 min)
# - AudioShake transcription (1-2 min)
# - spaCy model loading for correction (2-3 min on cold start)
# - Agentic AI correction (1-3 min)
TRANSCRIPTION_TIMEOUT_SECONDS = 1200

# Default agentic correction timeout (3 minutes)
# Configurable via AGENTIC_CORRECTION_TIMEOUT_SECONDS environment variable
# If agentic AI takes longer than this, abort and use uncorrected transcription
# Human review will correct any issues
DEFAULT_AGENTIC_TIMEOUT_SECONDS = 180


def _configure_agentic_ai():
    """Configure environment variables for agentic AI correction.

    The lyrics_transcriber library reads these directly from environment.
    This ensures the settings from backend config are available.
    """
    settings = get_settings()

    # Enable agentic AI if configured
    if settings.use_agentic_ai:
        os.environ["USE_AGENTIC_AI"] = "1"
        os.environ["AGENTIC_AI_MODEL"] = settings.agentic_ai_model
        logger.info(f"Agentic AI enabled with model: {settings.agentic_ai_model}")
    else:
        os.environ["USE_AGENTIC_AI"] = "0"
        logger.info("Agentic AI disabled")


# Loggers to capture for lyrics worker (top-level package loggers only)
# Note: We only capture at top-level package loggers to avoid duplicate log entries.
# Logs from sub-modules (e.g., lyrics_transcriber.correction.anchor_sequence) will
# propagate up to their parent logger (lyrics_transcriber) where they get captured.
LYRICS_WORKER_LOGGERS = [
    "karaoke_gen.lyrics_processor",
    "lyrics_transcriber",
]


def create_lyrics_processor(
    style_params_json: Optional[str] = None,
    lyrics_file: Optional[str] = None,
    subtitle_offset_ms: int = 0
) -> LyricsProcessor:
    """
    Create a LyricsProcessor instance configured for Cloud Run processing.
    
    This reuses the karaoke_gen LyricsProcessor with settings optimized for Cloud Run:
    - Uses AudioShake API for transcription (via AUDIOSHAKE_API_TOKEN env var)
    - Uses Genius/Spotify/Musixmatch APIs for reference lyrics (via env vars)
    - Skips interactive review (will be handled by separate React UI)
    - Generates corrections JSON for review interface
    
    Args:
        style_params_json: Optional path to style parameters JSON file
        lyrics_file: Optional path to user-provided lyrics file (overrides API fetch)
        subtitle_offset_ms: Offset for subtitle timing in milliseconds
        
    Returns:
        Configured LyricsProcessor instance
    """
    # Configure logger for LyricsProcessor
    lyrics_logger = logging.getLogger("karaoke_gen.lyrics_processor")
    lyrics_logger.setLevel(logging.INFO)
    
    return LyricsProcessor(
        logger=lyrics_logger,
        style_params_json=style_params_json,
        lyrics_file=lyrics_file,  # Use user-provided lyrics if available
        skip_transcription=False,  # We want transcription
        skip_transcription_review=True,  # Skip interactive review (use React UI instead)
        render_video=False,  # Skip video generation for now (will be done after review)
        subtitle_offset_ms=subtitle_offset_ms
    )


async def process_lyrics_transcription(job_id: str) -> bool:
    """
    Process lyrics transcription and correction for a job using karaoke_gen.LyricsProcessor.
    
    This is the main entry point for the lyrics worker.
    Called asynchronously from the job submission endpoint.
    
    Runs in parallel with audio_worker, coordinated via job state.
    
    Workflow:
    1. Download audio from GCS
    2. Create LyricsProcessor instance
    3. Run transcription (calls AudioShake API internally)
    4. Fetch reference lyrics from Genius/Spotify/Musixmatch/LRCLib
    5. Run automatic correction using lyrics_transcriber library
    6. Generate corrections JSON for review interface
    7. Upload results to GCS
    8. Transition to AWAITING_REVIEW state
    
    Args:
        job_id: Job ID to process
        
    Returns:
        True if successful, False otherwise
    """
    start_time = time.time()
    job_manager = JobManager()
    storage = StorageService()

    # Configure agentic AI before any lyrics processing
    _configure_agentic_ai()

    # Create job logger for remote debugging FIRST
    job_log = create_job_logger(job_id, "lyrics")
    
    # Log with structured markers for easy Cloud Logging queries
    logger.info(f"[job:{job_id}] WORKER_START worker=lyrics")
    job_log.info("=== LYRICS WORKER STARTED ===")
    job_log.info(f"Job ID: {job_id}")

    # Register with worker registry to prevent premature container shutdown
    # This ensures Cloud Run waits for this worker to complete before terminating
    await worker_registry.register(job_id, "lyrics")

    # Set up log capture for LyricsTranscriber and its dependencies
    # This ensures logs from the lyrics_transcriber library are also captured
    log_handler = setup_job_logging(job_id, "lyrics", *LYRICS_WORKER_LOGGERS)
    job_log.info(f"Log handler attached for {len(LYRICS_WORKER_LOGGERS)} loggers")
    
    # Initialize temp_dir before try block so finally can check it
    temp_dir = None
    
    try:
        # Wrap entire worker in a tracing span
        with job_span("lyrics-worker", job_id) as root_span:
            # Use job_logging_context for proper log isolation when multiple jobs run concurrently
            # This ensures logs from third-party libraries (lyrics_transcriber) are only captured
            # by this job's handler, not handlers from other concurrent jobs
            with job_logging_context(job_id):
                job = job_manager.get_job(job_id)
                if not job:
                    logger.error(f"[job:{job_id}] Job not found in Firestore")
                    job_log.error(f"Job {job_id} not found in Firestore!")
                    return False
                
                add_span_attribute("artist", job.artist)
                add_span_attribute("title", job.title)
                
                # Create temporary working directory
                temp_dir = tempfile.mkdtemp(prefix=f"karaoke_lyrics_{job_id}_")
                job_log.info(f"Created temp directory: {temp_dir}")
                job_log.info(f"Starting lyrics transcription for {job.artist} - {job.title}")
                job_log.info(f"Log capture enabled for: {', '.join(LYRICS_WORKER_LOGGERS)}")
                logger.info(f"[job:{job_id}] Starting lyrics transcription for {job.artist} - {job.title}")
                
                # Log environment configuration
                with job_span("check-api-config", job_id):
                    job_log.info("Checking API configuration...")
                    apis_configured = []
                    if os.environ.get("GENIUS_API_TOKEN"):
                        job_log.info("  Genius API: configured")
                        apis_configured.append("genius")
                    else:
                        job_log.warning("  Genius API: NOT configured")
                    if os.environ.get("SPOTIFY_COOKIE_SP_DC"):
                        job_log.info("  Spotify: configured")
                        apis_configured.append("spotify")
                    else:
                        job_log.warning("  Spotify: NOT configured")
                    if os.environ.get("RAPIDAPI_KEY"):
                        job_log.info("  Musixmatch (RapidAPI): configured")
                        apis_configured.append("musixmatch")
                    else:
                        job_log.warning("  Musixmatch (RapidAPI): NOT configured")
                    add_span_attribute("lyrics_apis", ",".join(apis_configured))
                
                # Ensure required environment variables are set
                if not os.environ.get("AUDIOSHAKE_API_TOKEN"):
                    job_log.warning("AUDIOSHAKE_API_TOKEN not set - transcription may fail")
                    logger.warning(f"[job:{job_id}] AUDIOSHAKE_API_TOKEN not set - transcription may fail")
                
                # Download audio file from GCS (waits for audio worker if URL job)
                with job_span("download-audio", job_id) as download_span:
                    job_log.info("Downloading audio file from GCS...")
                    audio_path = await download_audio(job_id, temp_dir, storage, job, job_manager)
                    if not audio_path:
                        raise Exception("Failed to download audio file")
                    job_log.info(f"Audio downloaded: {os.path.basename(audio_path)}")
                    download_span.set_attribute("audio_file", os.path.basename(audio_path))

                # Set up LyricsTranscriber cache directory and sync from GCS
                # This allows reusing cached AudioShake/lyrics API responses across Cloud Run instances
                cache_dir = os.path.join(temp_dir, "lyrics-cache")
                os.makedirs(cache_dir, exist_ok=True)
                os.environ["LYRICS_TRANSCRIBER_CACHE_DIR"] = cache_dir
                job_log.info(f"LyricsTranscriber cache dir: {cache_dir}")

                # Sync cache from GCS (download any existing cached API responses)
                # Note: Cache sync is best-effort - failures should not fail the job
                cache_service = LyricsCacheService(storage)
                audio_hash = None
                lyrics_hash = None
                with job_span("sync-cache-from-gcs", job_id):
                    try:
                        # Compute cache keys
                        audio_hash = cache_service.compute_audio_hash(audio_path)
                        # Use lyrics_artist/lyrics_title for lyrics hash (these are what LyricsTranscriber uses)
                        lyrics_search_artist = getattr(job, 'lyrics_artist', None) or job.artist
                        lyrics_search_title = getattr(job, 'lyrics_title', None) or job.title
                        lyrics_hash = cache_service.compute_lyrics_hash(lyrics_search_artist, lyrics_search_title)

                        job_log.info(f"Cache keys: audio_hash={audio_hash[:8]}..., lyrics_hash={lyrics_hash[:8]}...")
                        add_span_attribute("audio_hash", audio_hash[:8])
                        add_span_attribute("lyrics_hash", lyrics_hash[:8])

                        # Download relevant cache files from GCS
                        cache_stats = cache_service.sync_cache_from_gcs(cache_dir, audio_hash, lyrics_hash)
                        job_log.info(f"Cache sync from GCS: {cache_stats['downloaded']} downloaded, {cache_stats['not_found']} not found")
                        add_span_attribute("cache_hits", cache_stats["downloaded"])
                    except Exception as e:
                        job_log.warning(f"Cache sync from GCS failed (non-fatal): {e}", exc_info=True)
                        add_span_attribute("cache_sync_error", str(e)[:100])

                # Update progress using state_data (don't change status during parallel processing)
                # The status is managed at a higher level - workers just track their progress
                job_manager.update_state_data(job_id, 'lyrics_progress', {
                    'stage': 'transcribing',
                    'progress': 10,
                    'message': 'Starting lyrics transcription via AudioShake'
                })
                
                # Load style configuration (downloads assets from GCS if available)
                # This is needed for max_line_length and other style settings in LyricsTranscriber
                with job_span("load-style-config", job_id):
                    job_log.info("Loading style configuration for lyrics processing...")
                    style_config = await load_style_config(job, storage, temp_dir)
                    style_params_json_path = style_config.get_style_params_path() if style_config.has_custom_styles() else None
                    
                    if style_params_json_path:
                        job_log.info(f"Using custom style params: {style_params_json_path}")
                        # Log the contents of the style params for debugging
                        try:
                            with open(style_params_json_path, 'r') as f:
                                style_content = json.load(f)
                            job_log.info(f"Style params sections: {list(style_content.keys())}")
                            if 'karaoke' in style_content:
                                karaoke_style = style_content['karaoke']
                                job_log.info(f"  karaoke.background_image: {karaoke_style.get('background_image', 'NOT SET')}")
                                job_log.info(f"  karaoke.font_path: {karaoke_style.get('font_path', 'NOT SET')}")
                        except Exception as e:
                            job_log.warning(f"Could not read style params for logging: {e}")
                    else:
                        job_log.info("No custom style params found, using defaults")
                
                # Download user-provided lyrics file if available
                lyrics_file_path = None
                if hasattr(job, 'lyrics_file_gcs_path') and job.lyrics_file_gcs_path:
                    with job_span("download-user-lyrics", job_id):
                        lyrics_file_path = os.path.join(temp_dir, "user_lyrics.txt")
                        job_log.info(f"Downloading user-provided lyrics file: {job.lyrics_file_gcs_path}")
                        storage.download_file(job.lyrics_file_gcs_path, lyrics_file_path)
                        job_log.info(f"User lyrics file downloaded to: {lyrics_file_path}")
                
                # Get lyrics configuration from job
                subtitle_offset = getattr(job, 'subtitle_offset_ms', 0) or 0
                if subtitle_offset != 0:
                    job_log.info(f"Subtitle offset: {subtitle_offset}ms")
                    add_span_attribute("subtitle_offset_ms", subtitle_offset)
                
                # Create LyricsProcessor instance (reuses karaoke_gen code)
                job_log.info("Creating LyricsProcessor instance...")
                job_log.info(f"  style_params_json: {style_params_json_path}")
                job_log.info(f"  lyrics_file: {lyrics_file_path}")
                job_log.info(f"  subtitle_offset_ms: {subtitle_offset}")
                lyrics_processor = create_lyrics_processor(
                    style_params_json=style_params_json_path,
                    lyrics_file=lyrics_file_path,
                    subtitle_offset_ms=subtitle_offset
                )
                
                # Use lyrics_artist/lyrics_title overrides if provided, else fall back to job artist/title
                lyrics_search_artist = getattr(job, 'lyrics_artist', None) or job.artist
                lyrics_search_title = getattr(job, 'lyrics_title', None) or job.title
                
                job_log.info("Starting LyricsTranscriber processing...")
                job_log.info(f"  Artist: {job.artist}")
                job_log.info(f"  Title: {job.title}")
                if lyrics_search_artist != job.artist:
                    job_log.info(f"  Lyrics search artist override: {lyrics_search_artist}")
                if lyrics_search_title != job.title:
                    job_log.info(f"  Lyrics search title override: {lyrics_search_title}")
                logger.info(f"[job:{job_id}] Calling lyrics_processor.transcribe_lyrics()")

                # Run transcription + correction with timeout
                # AudioShake typically takes 1-2 minutes, but can hang.
                with job_span("audioshake-transcription", job_id) as trans_span:
                    trans_start = time.time()
                    add_span_event("transcription_started")

                    # Calculate deadline for agentic correction
                    # If agentic AI is enabled, the correction loop will check this deadline
                    # and abort early if exceeded, returning uncorrected transcription for human review
                    settings = get_settings()
                    agentic_deadline = None
                    timeout_seconds = settings.agentic_correction_timeout_seconds
                    if settings.use_agentic_ai:
                        agentic_deadline = time.time() + timeout_seconds
                        job_log.info(
                            f"Agentic AI enabled with {timeout_seconds}s timeout"
                        )

                    # Run transcription in thread pool
                    # When agentic AI is enabled, wrap with outer timeout as safety net
                    # Inner deadline check in corrector.py will break out of gap loop gracefully
                    # Outer timeout catches case where single LLM call hangs for too long
                    transcription_coro = asyncio.to_thread(
                        lyrics_processor.transcribe_lyrics,
                        input_audio_wav=audio_path,
                        artist=job.artist,  # Original artist for file naming
                        title=job.title,    # Original title for file naming
                        track_output_dir=temp_dir,
                        lyrics_artist=lyrics_search_artist,  # Override for lyrics search
                        lyrics_title=lyrics_search_title,    # Override for lyrics search
                        agentic_deadline=agentic_deadline,   # Deadline for agentic timeout
                    )

                    with metrics.time_external_api("audioshake", job_id):
                        if settings.use_agentic_ai:
                            # Outer timeout must be generous to allow for AudioShake time
                            # (can take 2-5+ minutes for long songs) PLUS agentic correction.
                            # The inner deadline check in corrector.py handles the 3-minute
                            # correction limit gracefully by returning partial results.
                            # This outer timeout is a safety net for completely hung LLM calls.
                            outer_timeout = TRANSCRIPTION_TIMEOUT_SECONDS  # Same as non-agentic
                            try:
                                result = await asyncio.wait_for(
                                    transcription_coro,
                                    timeout=outer_timeout
                                )
                            except asyncio.TimeoutError:
                                # This should rarely trigger since inner deadline check breaks first
                                # But provides hard stop if e.g. single LLM call hangs completely
                                job_log.error(
                                    f"HARD TIMEOUT: Transcription exceeded {outer_timeout}s. "
                                    "This indicates the inner deadline check failed to trigger."
                                )
                                raise RuntimeError(
                                    f"Lyrics transcription timed out after {outer_timeout}s"
                                ) from None
                        else:
                            # Non-agentic mode: use general timeout (10 minutes)
                            try:
                                result = await asyncio.wait_for(
                                    transcription_coro,
                                    timeout=TRANSCRIPTION_TIMEOUT_SECONDS
                                )
                            except asyncio.TimeoutError:
                                raise Exception(f"Transcription timed out after {TRANSCRIPTION_TIMEOUT_SECONDS} seconds")

                    trans_duration = time.time() - trans_start
                    trans_span.set_attribute("duration_seconds", trans_duration)
                    add_span_event("transcription_completed", {"duration_seconds": trans_duration})
                
                job_log.info("Transcription processing complete")
                logger.info(f"[job:{job_id}] Transcription complete, uploading results")
                
                # Upload lyrics results to GCS
                with job_span("upload-lyrics-results", job_id):
                    job_log.info("Uploading lyrics results to GCS...")
                    await upload_lyrics_results(job_id, temp_dir, result, storage, job_manager, job_log)
                
                job_log.info("All lyrics data uploaded successfully")
                logger.info(f"[job:{job_id}] All lyrics data uploaded successfully")

                # Sync new cache files to GCS (upload any newly created cache entries)
                # This persists cache across Cloud Run instances for future jobs
                # Note: Cache upload is best-effort - failures should not fail the job
                with job_span("sync-cache-to-gcs", job_id):
                    try:
                        if audio_hash and lyrics_hash:
                            upload_stats = cache_service.sync_cache_to_gcs(cache_dir, audio_hash, lyrics_hash)
                            job_log.info(f"Cache sync to GCS: {upload_stats['uploaded']} uploaded, {upload_stats['skipped']} already existed")
                            add_span_attribute("cache_uploads", upload_stats["uploaded"])
                        else:
                            job_log.warning("Skipping cache upload: missing audio_hash or lyrics_hash")
                            add_span_attribute("cache_upload_skipped", "missing_hashes")
                    except Exception as e:
                        job_log.warning(f"Cache upload to GCS failed (non-fatal): {e}")
                        add_span_attribute("cache_upload_error", str(e)[:100])

                # Update progress using state_data (don't change status during parallel processing)
                job_manager.update_state_data(job_id, 'lyrics_progress', {
                    'stage': 'lyrics_complete',
                    'progress': 45,
                    'message': 'Lyrics transcription complete'
                })
                
                # Mark lyrics processing complete
                # This will check if audio is also complete and transition to next stage if so
                job_log.info("Lyrics worker complete, checking if audio is also done...")
                job_manager.mark_lyrics_complete(job_id)
                
                duration = time.time() - start_time
                root_span.set_attribute("duration_seconds", duration)
                logger.info(f"[job:{job_id}] WORKER_END worker=lyrics status=success duration={duration:.1f}s")
                return True
        
    except Exception as e:
        duration = time.time() - start_time
        job_log.error(f"Lyrics transcription failed: {str(e)}", exc_info=True)
        logger.error(f"[job:{job_id}] WORKER_END worker=lyrics status=error duration={duration:.1f}s error={e}")
        job_manager.mark_job_failed(
            job_id=job_id,
            error_message=f"Lyrics transcription failed: {str(e)}",
            error_details={"stage": "lyrics_transcription", "error": str(e)}
        )
        return False
        
    finally:
        # Unregister from worker registry to signal completion
        await worker_registry.unregister(job_id, "lyrics")

        # Remove log handler to avoid duplicate logging on future runs
        for logger_name in LYRICS_WORKER_LOGGERS:
            try:
                logging.getLogger(logger_name).removeHandler(log_handler)
            except Exception:
                pass

        # Cleanup temporary directory (only if it was created)
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.debug(f"[job:{job_id}] Cleaned up temp directory: {temp_dir}")


async def download_audio(
    job_id: str,
    temp_dir: str,
    storage: StorageService,
    job,
    job_manager: JobManager,
    max_wait_seconds: int = 300
) -> Optional[str]:
    """
    Download audio file from GCS to local temp directory.
    
    For URL jobs, the audio worker downloads from YouTube first and uploads to GCS.
    We wait for the input_media_gcs_path to be set by the audio worker.
    
    Args:
        job_id: Job ID
        temp_dir: Temporary directory for download
        storage: StorageService instance
        job: Job object
        job_manager: JobManager instance
        max_wait_seconds: Maximum time to wait for audio worker to download
    
    Returns:
        Path to downloaded audio file, or None if failed
    """
    import time
    
    try:
        # If input_media_gcs_path is already set, download directly
        if job.input_media_gcs_path:
            local_path = os.path.join(temp_dir, job.filename or "input.flac")
            storage.download_file(job.input_media_gcs_path, local_path)
            logger.info(f"Job {job_id}: Downloaded audio from {job.input_media_gcs_path} to {local_path}")
            return local_path
        
        # For URL jobs, wait for audio worker to download and upload to GCS
        if job.url:
            logger.info(f"Job {job_id}: Waiting for audio worker to download from URL...")
            
            start_time = time.time()
            poll_interval = 5  # seconds
            
            while time.time() - start_time < max_wait_seconds:
                # Refresh job from Firestore
                updated_job = job_manager.get_job(job_id)
                
                if updated_job and updated_job.input_media_gcs_path:
                    # Audio worker has uploaded the file
                    local_path = os.path.join(temp_dir, "input.flac")
                    storage.download_file(updated_job.input_media_gcs_path, local_path)
                    logger.info(f"Job {job_id}: Downloaded audio from {updated_job.input_media_gcs_path}")
                    return local_path
                
                # Check if audio worker failed
                if updated_job and updated_job.status == JobStatus.FAILED:
                    logger.error(f"Job {job_id}: Audio worker failed, cannot proceed with lyrics")
                    return None
                
                # Wait before next poll - use asyncio.sleep to not block event loop
                await asyncio.sleep(poll_interval)
            
            logger.error(f"Job {job_id}: Timed out waiting for audio download (waited {max_wait_seconds}s)")
            return None
        
        logger.error(f"Job {job_id}: No input_media_gcs_path found and no URL")
        return None
        
    except Exception as e:
        logger.error(f"Job {job_id}: Failed to download audio: {e}", exc_info=True)
        return None


async def upload_lyrics_results(
    job_id: str,
    temp_dir: str,
    transcription_result: Dict[str, Any],
    storage: StorageService,
    job_manager: JobManager,
    job_log = None
) -> None:
    """
    Upload all lyrics transcription results to GCS.
    
    The transcription_result dict from LyricsProcessor.transcribe_lyrics() contains:
    - lrc_filepath: Path to LRC file (timed lyrics)
    - ass_filepath: Path to ASS file (karaoke subtitles, may be video file)
    
    Additional files in lyrics directory:
    - Corrections JSON (for review interface)
    - Reference lyrics (from Genius/Spotify/Musixmatch/LRCLib)
    - Uncorrected transcription
    
    Args:
        job_id: Job ID
        temp_dir: Temporary directory with results
        transcription_result: Result dict from LyricsProcessor
        storage: StorageService instance
        job_manager: JobManager instance
        job_log: Optional JobLogger for remote debugging
    """
    logger.info(f"Job {job_id}: Uploading lyrics results to GCS")
    
    # Get job object for artist/title
    job = job_manager.get_job(job_id)
    
    lyrics_dir = os.path.join(temp_dir, "lyrics")
    
    # Upload LRC file (timed lyrics)
    if transcription_result.get("lrc_filepath") and os.path.exists(transcription_result["lrc_filepath"]):
        gcs_path = f"jobs/{job_id}/lyrics/karaoke.lrc"
        url = storage.upload_file(transcription_result["lrc_filepath"], gcs_path)
        job_manager.update_file_url(job_id, 'lyrics', 'lrc', url)
        logger.info(f"Job {job_id}: Uploaded LRC file")
    
    # Upload corrections JSON (for review interface)
    # LyricsProcessor saves it as "{artist} - {title} (Lyrics Corrections).json"
    # Sanitize artist/title to prevent path injection and match LyricsProcessor's sanitization
    safe_artist = sanitize_filename(job.artist) if job.artist else "Unknown"
    safe_title = sanitize_filename(job.title) if job.title else "Unknown"
    corrections_filename = f"{safe_artist} - {safe_title} (Lyrics Corrections).json"
    corrections_file = os.path.join(lyrics_dir, corrections_filename)
    
    # Also check for generic corrections.json (fallback)
    if not os.path.exists(corrections_file):
        corrections_file = os.path.join(lyrics_dir, "corrections.json")
    
    if os.path.exists(corrections_file):
        # Always upload as corrections.json for consistent access
        gcs_path = f"jobs/{job_id}/lyrics/corrections.json"
        url = storage.upload_file(corrections_file, gcs_path)
        job_manager.update_file_url(job_id, 'lyrics', 'corrections', url)
        logger.info(f"Job {job_id}: Uploaded corrections JSON from {corrections_file}")
        
        # Load corrections to get metadata
        try:
            with open(corrections_file, 'r', encoding='utf-8') as f:
                corrections_data = json.load(f)
            
            # Store metadata in state_data
            # Include countdown padding info for instrumental synchronization
            lyrics_metadata = {
                'segment_count': len(corrections_data.get('corrected_segments', [])),
                'has_corrections': True,
                'ready_for_review': True,
            }
            
            # Add countdown padding info from transcription result
            # This is needed by video_worker to pad instrumentals for sync
            if transcription_result.get("countdown_padding_added"):
                lyrics_metadata['has_countdown_padding'] = True
                lyrics_metadata['countdown_padding_seconds'] = transcription_result.get("countdown_padding_seconds", 3.0)
                logger.info(f"Job {job_id}: Countdown padding detected: {lyrics_metadata['countdown_padding_seconds']}s")
            else:
                lyrics_metadata['has_countdown_padding'] = False
                lyrics_metadata['countdown_padding_seconds'] = 0.0
            
            job_manager.update_state_data(job_id, 'lyrics_metadata', lyrics_metadata)
        except Exception as e:
            logger.warning(f"Job {job_id}: Could not parse corrections JSON: {e}")
    else:
        # CRITICAL: corrections.json is required for the review UI
        # If it's missing, the job cannot proceed to review
        error_msg = f"No corrections JSON found at {corrections_file}. Transcription may have produced no lyrics."
        logger.error(f"Job {job_id}: {error_msg}")
        raise Exception(error_msg)
    
    # Upload ALL reference lyrics files (not just first) so they're available for distribution
    # Note: Source names use .title() so "lrclib" -> "Lrclib", "genius" -> "Genius"
    # Use sanitized artist/title to match LyricsProcessor's file naming
    reference_files = [
        (f"{safe_artist} - {safe_title} (Lyrics Genius).txt", "genius"),
        (f"{safe_artist} - {safe_title} (Lyrics Spotify).txt", "spotify"),
        (f"{safe_artist} - {safe_title} (Lyrics Musixmatch).txt", "musixmatch"),
        (f"{safe_artist} - {safe_title} (Lyrics Lrclib).txt", "lrclib"),
    ]

    found_references = []
    for ref_filename, source_key in reference_files:
        ref_path = os.path.join(lyrics_dir, ref_filename)
        if os.path.exists(ref_path):
            # Upload with original filename to preserve proper naming
            gcs_path = f"jobs/{job_id}/lyrics/{ref_filename}"
            url = storage.upload_file(ref_path, gcs_path)
            # Track in job.files so video_worker can download for distribution
            job_manager.update_file_url(job_id, 'lyrics', f'reference_{source_key}', url)
            if job_log:
                job_log.info(f"Found reference lyrics from {source_key}")
            logger.info(f"Job {job_id}: Uploaded reference lyrics: {ref_filename}")
            found_references.append(source_key)

    if not found_references:
        if job_log:
            job_log.warning("No reference lyrics found from any source (Genius, Spotify, Musixmatch, LRCLib)")
        logger.warning(f"Job {job_id}: No reference lyrics found from any source")
    else:
        logger.info(f"Job {job_id}: Found {len(found_references)} reference lyrics sources: {found_references}")
    
    # Upload uncorrected transcription if available (preserve original filename for distribution)
    uncorrected_filename = f"{safe_artist} - {safe_title} (Lyrics Uncorrected).txt"
    uncorrected_file = os.path.join(lyrics_dir, uncorrected_filename)
    if os.path.exists(uncorrected_file):
        gcs_path = f"jobs/{job_id}/lyrics/{uncorrected_filename}"
        url = storage.upload_file(uncorrected_file, gcs_path)
        job_manager.update_file_url(job_id, 'lyrics', 'uncorrected', url)
        logger.info(f"Job {job_id}: Uploaded uncorrected transcription: {uncorrected_filename}")
    
    logger.info(f"Job {job_id}: All lyrics results uploaded successfully")


# ==================== CLI Entry Point for Cloud Run Jobs ====================
# This allows the lyrics worker to be run as a standalone Cloud Run Job.
# Usage: python -m backend.workers.lyrics_worker --job-id <job_id>

def main():
    """
    CLI entry point for running lyrics worker as a Cloud Run Job.

    Cloud Run Jobs run to completion without HTTP request lifecycle concerns,
    avoiding the instance termination issue where Cloud Run would shut down
    instances mid-processing when using BackgroundTasks.

    Usage:
        python -m backend.workers.lyrics_worker --job-id abc123

    Environment Variables:
        GOOGLE_CLOUD_PROJECT: GCP project ID (required)
        GCS_BUCKET_NAME: Storage bucket name (required)
        AUDIOSHAKE_API_TOKEN: AudioShake API key (required)
    """
    import argparse
    import sys

    # Set up argument parser
    parser = argparse.ArgumentParser(
        description="Lyrics transcription worker for karaoke generation"
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

    logger.info(f"Starting lyrics worker CLI for job {job_id}")

    # Run the async worker function
    try:
        success = asyncio.run(process_lyrics_transcription(job_id))
        if success:
            logger.info(f"Lyrics transcription completed successfully for job {job_id}")
            sys.exit(0)
        else:
            logger.error(f"Lyrics transcription failed for job {job_id}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Lyrics worker crashed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

