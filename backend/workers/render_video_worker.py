"""
Render Video Worker

Generates the karaoke video with synchronized lyrics AFTER human review.

This worker:
1. Downloads the corrected lyrics data from GCS
2. Downloads the audio file
3. Downloads style assets from GCS
4. Uses LyricsTranscriber's OutputGenerator to render video
5. Uploads the with_vocals.mkv to GCS
6. Transitions to INSTRUMENTAL_SELECTED (user already selected during combined review)
7. Triggers video worker for final encoding

Note: Instrumental selection now happens during the combined lyrics + instrumental
review phase (before this worker runs). The selection is stored in
state_data['instrumental_selection'] by the review completion endpoint.

Key insight: We use OutputGenerator from lyrics_transcriber library
WITHOUT using its blocking ReviewServer. This allows async operation
in Cloud Run.

Observability:
- All operations wrapped in tracing spans for Cloud Trace visibility
- Logs include [job:ID] prefix for easy filtering in Cloud Logging
- Worker start/end timing logged with WORKER_START/WORKER_END markers
"""
import logging
import os
import tempfile
import time
import json
from typing import Optional, Dict, Any
from dataclasses import dataclass

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.job_health_service import validate_worker_can_run
from backend.config import get_settings
from backend.workers.worker_logging import create_job_logger, setup_job_logging, job_logging_context
from backend.services.tracing import job_span, add_span_event, add_span_attribute
from backend.services.encoding_service import get_encoding_service

# Import from lyrics_transcriber (submodule)
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
from karaoke_gen.lyrics_transcriber.types import CorrectionResult
from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig

# Import from the unified style loader
from karaoke_gen.style_loader import load_styles_from_gcs
from karaoke_gen.utils import sanitize_filename


logger = logging.getLogger(__name__)


# Loggers to capture for render video worker
RENDER_VIDEO_WORKER_LOGGERS = [
    "karaoke_gen.lyrics_transcriber.output",
    "karaoke_gen.lyrics_transcriber.output.generator",
    "karaoke_gen.lyrics_transcriber.output.video",
    "karaoke_gen.lyrics_transcriber.output.ass",
]


async def process_render_video(job_id: str) -> bool:
    """
    Render karaoke video with corrected lyrics.
    
    Called after human review is complete (REVIEW_COMPLETE state).
    Uses OutputGenerator from lyrics_transcriber to generate the video.
    
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
    job_log = create_job_logger(job_id, "render_video")
    
    # Log with structured markers for easy Cloud Logging queries
    logger.info(f"[job:{job_id}] WORKER_START worker=render-video")
    job_log.info("=== RENDER VIDEO WORKER STARTED ===")
    job_log.info(f"Job ID: {job_id}")
    
    # Set up log capture for OutputGenerator
    log_handler = setup_job_logging(job_id, "render_video", *RENDER_VIDEO_WORKER_LOGGERS)
    job_log.info(f"Log handler attached for {len(RENDER_VIDEO_WORKER_LOGGERS)} loggers")
    
    job = job_manager.get_job(job_id)
    if not job:
        logger.error(f"[job:{job_id}] Job not found in Firestore")
        job_log.error(f"Job {job_id} not found in Firestore!")
        return False

    # Validate job status is appropriate for render video worker
    # This helps catch bugs where the worker is triggered incorrectly
    status_error = validate_worker_can_run("render_video_worker", job)
    if status_error:
        logger.warning(f"[job:{job_id}] {status_error}")
        job_log.warning(status_error)
        # Continue anyway - this is a safety net warning, not a hard failure

    job_log.info(f"Starting video render for {job.artist} - {job.title}")
    logger.info(f"[job:{job_id}] Starting video render (post-review)")
    
    try:
        # Wrap entire worker in a tracing span
        with job_span("render-video-worker", job_id, {"artist": job.artist, "title": job.title}) as root_span:
            # Use job_logging_context for proper log isolation when multiple jobs run concurrently
            # This ensures logs from third-party libraries (lyrics_transcriber.output) are only
            # captured by this job's handler, not handlers from other concurrent jobs
            with job_logging_context(job_id):
                # Check if GCE encoding is enabled
                encoding_service = get_encoding_service()
                use_gce = encoding_service.is_enabled

                if use_gce:
                    # ============ GCE RENDER VIDEO PATH ============
                    job_log.info("GCE encoding enabled - delegating render_video to encoding worker")
                    logger.info(f"[job:{job_id}] Using GCE encoding worker for render_video")

                    # Transition to RENDERING_VIDEO
                    job_manager.transition_to_state(
                        job_id=job_id,
                        new_status=JobStatus.RENDERING_VIDEO,
                        progress=75,
                        message="Rendering karaoke video on encoding worker"
                    )

                    # Build render config from job metadata (no downloads needed on Cloud Run!)
                    bucket_name = settings.gcs_bucket_name

                    # Check for updated corrections
                    updated_corrections_gcs = f"jobs/{job_id}/lyrics/corrections_updated.json"
                    has_updated = storage.file_exists(updated_corrections_gcs)

                    # Read is_duet from state_data (set by review completion endpoint)
                    is_duet = bool(job.state_data.get("is_duet", False))

                    render_config = {
                        "original_corrections_gcs_path": f"gs://{bucket_name}/jobs/{job_id}/lyrics/corrections.json",
                        "audio_gcs_path": f"gs://{bucket_name}/{job.input_media_gcs_path}",
                        "output_gcs_prefix": f"gs://{bucket_name}/jobs/{job_id}/",
                        "artist": job.artist,
                        "title": job.title,
                        "subtitle_offset_ms": getattr(job, 'subtitle_offset_ms', 0) or 0,
                        "video_resolution": "4k",
                        "is_duet": is_duet,
                    }

                    if has_updated:
                        render_config["updated_corrections_gcs_path"] = f"gs://{bucket_name}/{updated_corrections_gcs}"

                    if job.style_params_gcs_path:
                        render_config["style_params_gcs_path"] = f"gs://{bucket_name}/{job.style_params_gcs_path}" if not job.style_params_gcs_path.startswith("gs://") else job.style_params_gcs_path

                    if job.style_assets:
                        render_config["style_assets"] = {
                            k: f"gs://{bucket_name}/{v}" if not v.startswith("gs://") else v
                            for k, v in job.style_assets.items()
                        }

                    def progress_callback(progress: int):
                        scaled = 75 + int(progress * 0.07)  # Map 0-100 to 75-82
                        job_manager.transition_to_state(
                            job_id=job_id,
                            new_status=JobStatus.RENDERING_VIDEO,
                            progress=scaled,
                            message=f"Rendering video ({progress}%)"
                        )

                    with job_span("gce-render-video", job_id) as render_span:
                        render_start = time.time()
                        result = await encoding_service.render_video_on_gce(
                            job_id, render_config, progress_callback=progress_callback
                        )
                        render_duration = time.time() - render_start
                        render_span.set_attribute("duration_seconds", render_duration)

                    job_log.info(f"GCE render_video complete in {render_duration:.1f}s")
                    logger.info(f"[job:{job_id}] GCE render_video complete in {render_duration:.1f}s")

                    # Parse response and update Firestore
                    output_files = result.get("output_files", [])
                    metadata = result.get("metadata", {})

                    # Update file URLs from GCE response
                    for gcs_path in output_files:
                        blob_path = gcs_path
                        if blob_path.startswith("gs://"):
                            blob_path = blob_path.split("/", 3)[3] if blob_path.count("/") >= 3 else blob_path
                        if "with_vocals.mkv" in blob_path:
                            job_manager.update_file_url(job_id, 'videos', 'with_vocals', blob_path)
                        elif "karaoke.ass" in blob_path:
                            job_manager.update_file_url(job_id, 'lyrics', 'ass', blob_path)
                        elif "karaoke.lrc" in blob_path:
                            job_manager.update_file_url(job_id, 'lyrics', 'lrc', blob_path)
                        elif "corrected.txt" in blob_path:
                            job_manager.update_file_url(job_id, 'lyrics', 'corrected_txt', blob_path)

                    # Handle countdown padding metadata
                    padding_added = metadata.get("countdown_padding_added", False)
                    padding_seconds = metadata.get("countdown_padding_seconds", 0)
                    if padding_added:
                        existing_lyrics_metadata = job.state_data.get('lyrics_metadata', {})
                        existing_lyrics_metadata['has_countdown_padding'] = True
                        existing_lyrics_metadata['countdown_padding_seconds'] = padding_seconds
                        job_manager.update_state_data(job_id, 'lyrics_metadata', existing_lyrics_metadata)
                        job_log.info(f"Countdown padding: {padding_seconds}s")

                    # Store render timing
                    job_manager.update_processing_metadata(job_id, "rendering", {
                        "render_duration_seconds": round(render_duration, 1),
                        "countdown_padding_added": padding_added,
                        "countdown_padding_seconds": padding_seconds if padding_added else 0,
                        "rendered_on": "gce",
                    })
                    job_manager.update_processing_metadata(job_id, "timing.render_video_worker_seconds", round(render_duration, 1))

                    # Transition state (same as local path)
                    if getattr(job, 'prep_only', False):
                        job_manager.transition_to_state(
                            job_id=job_id,
                            new_status=JobStatus.PREP_COMPLETE,
                            progress=100,
                            message="Prep phase complete - download outputs to continue locally"
                        )
                        job_log.info("=== RENDER VIDEO WORKER COMPLETE (PREP ONLY, GCE) ===")
                        duration = time.time() - start_time
                        root_span.set_attribute("duration_seconds", duration)
                        root_span.set_attribute("rendered_on", "gce")
                        logger.info(f"[job:{job_id}] WORKER_END worker=render-video status=success duration={duration:.1f}s gce=true prep_only=true")
                    else:
                        job_manager.transition_to_state(
                            job_id=job_id,
                            new_status=JobStatus.INSTRUMENTAL_SELECTED,
                            progress=82,
                            message="Video rendered on encoding worker, starting final encoding"
                        )
                        job_log.info("=== RENDER VIDEO WORKER COMPLETE (GCE) ===")
                        duration = time.time() - start_time
                        root_span.set_attribute("duration_seconds", duration)
                        root_span.set_attribute("rendered_on", "gce")
                        logger.info(f"[job:{job_id}] WORKER_END worker=render-video status=success duration={duration:.1f}s gce=true")

                        from backend.services.worker_service import get_worker_service
                        worker_service = get_worker_service()
                        job_log.info("Triggering video worker for final encoding...")
                        await worker_service.trigger_video_worker(job_id)

                    job_manager.update_state_data(job_id, 'render_progress', {'stage': 'complete'})
                    return True

                else:
                    # ============ LOCAL RENDER VIDEO PATH ============
                    # Transition to RENDERING_VIDEO
                    job_manager.transition_to_state(
                        job_id=job_id,
                        new_status=JobStatus.RENDERING_VIDEO,
                        progress=75,
                        message="Rendering karaoke video with corrected lyrics"
                    )

                    with tempfile.TemporaryDirectory() as temp_dir:
                        job_log.info(f"Created temp directory: {temp_dir}")

                        # Try updated corrections first (from human review), fall back to original
                        corrections_gcs_updated = f"jobs/{job_id}/lyrics/corrections_updated.json"
                        corrections_gcs_original = f"jobs/{job_id}/lyrics/corrections.json"

                        # Get GCS path from file_urls
                        corrections_url = job.file_urls.get('lyrics', {}).get('corrections_updated')
                        if not corrections_url:
                            corrections_url = job.file_urls.get('lyrics', {}).get('corrections')

                        if not corrections_url:
                            # Try direct GCS paths
                            if storage.file_exists(corrections_gcs_updated):
                                corrections_gcs = corrections_gcs_updated
                            elif storage.file_exists(corrections_gcs_original):
                                corrections_gcs = corrections_gcs_original
                            else:
                                raise FileNotFoundError(f"No corrections file found for job {job_id}")
                        else:
                            # Extract GCS path from URL
                            corrections_gcs = _extract_gcs_path(corrections_url)

                        # 2. Load the ORIGINAL corrections (has full structure)
                        original_corrections_gcs = f"jobs/{job_id}/lyrics/corrections.json"
                        original_corrections_path = os.path.join(temp_dir, "corrections_original.json")

                        job_log.info(f"Downloading original corrections from {original_corrections_gcs}")
                        logger.info(f"Job {job_id}: Downloading original corrections from {original_corrections_gcs}")
                        storage.download_file(original_corrections_gcs, original_corrections_path)

                        with open(original_corrections_path, 'r', encoding='utf-8') as f:
                            original_data = json.load(f)

                        # 3. Load base CorrectionResult from original data
                        base_result = CorrectionResult.from_dict(original_data)

                        # 4. Apply user corrections if available (from review UI)
                        # Uses the same method as preview generation to ensure
                        # identical CorrectionResult construction (DRY).
                        updated_corrections_gcs = f"jobs/{job_id}/lyrics/corrections_updated.json"

                        if storage.file_exists(updated_corrections_gcs):
                            job_log.info("Found updated corrections from review, applying via shared method")
                            logger.info(f"Job {job_id}: Found updated corrections, applying via shared method")
                            updated_path = os.path.join(temp_dir, "corrections_updated.json")
                            storage.download_file(updated_corrections_gcs, updated_path)

                            with open(updated_path, 'r', encoding='utf-8') as f:
                                updated_data = json.load(f)

                            if isinstance(updated_data, dict) and "corrections" in updated_data:
                                # Same construction path as preview — prevents divergence
                                correction_result = CorrectionOperations.update_correction_result_with_data(
                                    base_result, updated_data
                                )
                                job_log.info("Applied user corrections via CorrectionOperations")
                                logger.info(f"Job {job_id}: Applied user corrections via CorrectionOperations")
                            else:
                                job_log.warning("corrections_updated.json exists but has no 'corrections' key, using base result")
                                logger.warning(f"Job {job_id}: corrections_updated.json has no 'corrections' key, using base result")
                                correction_result = base_result
                        else:
                            correction_result = base_result

                        job_log.info(f"Loaded CorrectionResult with {len(correction_result.corrected_segments)} segments")
                        logger.info(f"Job {job_id}: Loaded CorrectionResult with {len(correction_result.corrected_segments)} segments")

                        # 5. Download audio file
                        audio_path = os.path.join(temp_dir, "audio.flac")
                        audio_gcs_path = job.input_media_gcs_path

                        if not audio_gcs_path:
                            raise FileNotFoundError(f"No input audio path for job {job_id}")

                        job_log.info(f"Downloading audio from {audio_gcs_path}")
                        logger.info(f"Job {job_id}: Downloading audio from {audio_gcs_path}")
                        storage.download_file(audio_gcs_path, audio_path)
                        job_log.info(f"Audio downloaded: {os.path.getsize(audio_path)} bytes")

                        # Process countdown intro if needed (for songs that start too quickly)
                        # This adds "3... 2... 1..." segment and pads audio with 3s silence
                        # Note: Countdown is deferred to this stage (not during lyrics transcription)
                        # so the review UI can show the original timing without the 3s shift
                        countdown_processor = CountdownProcessor(cache_dir=temp_dir, logger=logger)
                        correction_result, audio_path, padding_added, padding_seconds = countdown_processor.process(
                            correction_result=correction_result,
                            audio_filepath=audio_path,
                        )
                        if padding_added:
                            job_log.info(f"Countdown added: {padding_seconds}s padding applied to audio and timestamps shifted")
                        else:
                            job_log.info("No countdown needed - song starts after 3 seconds")

                        # Update lyrics_metadata with countdown info so video_worker knows to pad instrumental
                        # This is critical for audio sync - without this, instrumental won't match padded vocals
                        if padding_added:
                            existing_lyrics_metadata = job.state_data.get('lyrics_metadata', {})
                            existing_lyrics_metadata['has_countdown_padding'] = True
                            existing_lyrics_metadata['countdown_padding_seconds'] = padding_seconds
                            job_manager.update_state_data(job_id, 'lyrics_metadata', existing_lyrics_metadata)
                            job_log.info(f"Updated lyrics_metadata: has_countdown_padding=True, countdown_padding_seconds={padding_seconds}")

                        # Read is_duet from state_data (set by review completion endpoint)
                        is_duet = bool(job.state_data.get("is_duet", False))
                        if is_duet:
                            job_log.info("Duet mode enabled: using two-singer subtitle layout")

                        # 6. Get or create styles using the unified style loader
                        job_log.info("Loading style configuration...")
                        job_log.info(f"  job.style_params_gcs_path: {job.style_params_gcs_path}")
                        job_log.info(f"  job.style_assets: {list(job.style_assets.keys()) if job.style_assets else 'None'}")

                        styles_path, style_data = load_styles_from_gcs(
                            style_params_gcs_path=job.style_params_gcs_path,
                            style_assets=job.style_assets,
                            temp_dir=temp_dir,
                            download_func=storage.download_file,
                            logger=job_log,
                        )

                        # 7. Configure OutputGenerator
                        output_dir = os.path.join(temp_dir, "output")
                        cache_dir = os.path.join(temp_dir, "cache")
                        os.makedirs(output_dir, exist_ok=True)
                        os.makedirs(cache_dir, exist_ok=True)

                        job_log.info(f"Using styles from: {styles_path}")

                        # Get subtitle offset from job (user-specified timing adjustment)
                        subtitle_offset = getattr(job, 'subtitle_offset_ms', 0) or 0
                        if subtitle_offset != 0:
                            job_log.info(f"Applying subtitle offset: {subtitle_offset}ms")

                        config = OutputConfig(
                            output_dir=output_dir,
                            cache_dir=cache_dir,
                            output_styles_json=styles_path,
                            render_video=True,
                            generate_cdg=False,  # CDG optional, generated separately
                            generate_plain_text=True,
                            generate_lrc=True,
                            video_resolution="4k",
                            subtitle_offset_ms=subtitle_offset,
                            is_duet=is_duet,
                        )

                        job_log.info(f"OutputConfig: output_styles_json={config.output_styles_json}, render_video={config.render_video}")

                        output_generator = OutputGenerator(config, logger)

                        # 8. Generate outputs (video, LRC, ASS, etc.)
                        # Sanitize artist/title to handle Unicode characters (curly quotes, em dashes, etc.)
                        safe_artist = sanitize_filename(job.artist) if job.artist else "Unknown"
                        safe_title = sanitize_filename(job.title) if job.title else "Unknown"
                        output_prefix = f"{safe_artist} - {safe_title}"
                        job_log.info(f"Generating outputs with prefix '{output_prefix}'")
                        logger.info(f"Job {job_id}: Generating outputs with prefix '{output_prefix}'")

                        outputs = output_generator.generate_outputs(
                            transcription_corrected=correction_result,
                            lyrics_results={},  # Reference lyrics already in correction_result
                            output_prefix=output_prefix,
                            audio_filepath=audio_path,
                            artist=job.artist,
                            title=job.title
                        )

                        # 9. Upload video to GCS
                        if outputs.video and os.path.exists(outputs.video):
                            video_size = os.path.getsize(outputs.video)
                            job_log.info(f"Video generated: {video_size} bytes")
                            video_gcs_path = f"jobs/{job_id}/videos/with_vocals.mkv"
                            video_url = storage.upload_file(outputs.video, video_gcs_path)
                            job_manager.update_file_url(job_id, 'videos', 'with_vocals', video_url)
                            job_log.info(f"Uploaded with_vocals.mkv to GCS")
                            logger.info(f"Job {job_id}: Uploaded with_vocals.mkv ({video_size} bytes)")
                        else:
                            job_log.error("Video generation failed - no output file produced!")
                            raise Exception("Video generation failed - no output file produced")

                        # 10. Upload LRC file
                        if outputs.lrc and os.path.exists(outputs.lrc):
                            lrc_gcs_path = f"jobs/{job_id}/lyrics/karaoke.lrc"
                            lrc_url = storage.upload_file(outputs.lrc, lrc_gcs_path)
                            job_manager.update_file_url(job_id, 'lyrics', 'lrc', lrc_url)
                            job_log.info("Uploaded karaoke.lrc")
                            logger.info(f"Job {job_id}: Uploaded karaoke.lrc")

                        # 11. Upload ASS subtitle file
                        if outputs.ass and os.path.exists(outputs.ass):
                            ass_gcs_path = f"jobs/{job_id}/lyrics/karaoke.ass"
                            ass_url = storage.upload_file(outputs.ass, ass_gcs_path)
                            job_manager.update_file_url(job_id, 'lyrics', 'ass', ass_url)
                            job_log.info("Uploaded karaoke.ass")
                            logger.info(f"Job {job_id}: Uploaded karaoke.ass")

                        # 12. Upload corrected text files
                        if outputs.corrected_txt and os.path.exists(outputs.corrected_txt):
                            txt_gcs_path = f"jobs/{job_id}/lyrics/corrected.txt"
                            txt_url = storage.upload_file(outputs.corrected_txt, txt_gcs_path)
                            job_manager.update_file_url(job_id, 'lyrics', 'corrected_txt', txt_url)
                            job_log.info("Uploaded corrected.txt")
                            logger.info(f"Job {job_id}: Uploaded corrected.txt")

                        # Store render timing in processing metadata
                        render_duration = time.time() - start_time
                        job_manager.update_processing_metadata(job_id, "rendering", {
                            "render_duration_seconds": round(render_duration, 1),
                            "countdown_padding_added": padding_added,
                            "countdown_padding_seconds": padding_seconds if padding_added else 0,
                        })
                        job_manager.update_processing_metadata(job_id, "timing.render_video_worker_seconds", round(render_duration, 1))

                        # 13. Transition based on prep_only flag
                        # Note: Instrumental selection was already made during combined review
                        # (stored in state_data['instrumental_selection'])
                        if getattr(job, 'prep_only', False):
                            # Prep-only mode: stop here and mark as prep complete
                            job_manager.transition_to_state(
                                job_id=job_id,
                                new_status=JobStatus.PREP_COMPLETE,
                                progress=100,
                                message="Prep phase complete - download outputs to continue locally"
                            )
                            job_log.info("=== RENDER VIDEO WORKER COMPLETE (PREP ONLY) ===")
                            duration = time.time() - start_time
                            root_span.set_attribute("duration_seconds", duration)
                            root_span.set_attribute("prep_only", True)
                            logger.info(f"[job:{job_id}] WORKER_END worker=render-video status=success duration={duration:.1f}s prep_only=true")
                        else:
                            # Normal mode: instrumental was selected during combined review
                            # Transition directly to INSTRUMENTAL_SELECTED and trigger video worker
                            job_manager.transition_to_state(
                                job_id=job_id,
                                new_status=JobStatus.INSTRUMENTAL_SELECTED,
                                progress=82,
                                message="Video rendered, starting final encoding"
                            )
                            job_log.info("=== RENDER VIDEO WORKER COMPLETE ===")
                            duration = time.time() - start_time
                            root_span.set_attribute("duration_seconds", duration)
                            logger.info(f"[job:{job_id}] WORKER_END worker=render-video status=success duration={duration:.1f}s")

                            # Trigger video worker for final encoding
                            from backend.services.worker_service import get_worker_service

                            worker_service = get_worker_service()
                            job_log.info("Triggering video worker for final encoding...")
                            await worker_service.trigger_video_worker(job_id)

                        # Mark render progress as complete for idempotency
                        # This allows the worker to be re-triggered after admin reset
                        job_manager.update_state_data(job_id, 'render_progress', {'stage': 'complete'})
                        return True
            
    except Exception as e:
        duration = time.time() - start_time
        job_log.error(f"Video render failed: {e}")
        logger.error(f"[job:{job_id}] WORKER_END worker=render-video status=error duration={duration:.1f}s error={e}")
        job_manager.fail_job(job_id, f"Video render failed: {str(e)}")
        return False


def _extract_gcs_path(url: str) -> str:
    """Extract GCS path from a URL or return as-is if already a path."""
    if url.startswith('gs://'):
        # Already a GCS path
        return url.replace('gs://', '').split('/', 1)[1] if '/' in url else url
    if url.startswith('https://storage.googleapis.com/'):
        # Signed URL - extract path
        path = url.replace('https://storage.googleapis.com/', '')
        # Remove query params
        if '?' in path:
            path = path.split('?')[0]
        # Skip bucket name
        parts = path.split('/', 1)
        return parts[1] if len(parts) > 1 else path
    return url


# For compatibility with worker service
render_video_worker = process_render_video
