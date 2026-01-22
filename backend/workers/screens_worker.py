"""
Title and end screen generation worker.

Handles screen generation after parallel processing completes:
1. Generate title screen with artist/song info
2. Generate end screen ("Thank you for singing!")
3. Upload both screens to GCS
4. Transition to AWAITING_REVIEW (human must review lyrics)

After review:
5. Render video worker generates with_vocals.mkv
6. Then AWAITING_INSTRUMENTAL_SELECTION

This worker is triggered automatically when both audio and lyrics
processing complete (via mark_audio_complete/mark_lyrics_complete coordination).

Integrates with karaoke_gen.video_generator.VideoGenerator.

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
from backend.workers.style_helper import load_style_config, StyleConfig
from backend.workers.worker_logging import create_job_logger, setup_job_logging, job_logging_context
from backend.services.tracing import job_span, add_span_event, add_span_attribute

# Import from karaoke_gen package
from karaoke_gen.video_generator import VideoGenerator
from karaoke_gen.utils import sanitize_filename


logger = logging.getLogger(__name__)


# Loggers to capture for screens worker
SCREENS_WORKER_LOGGERS = [
    "karaoke_gen.video_generator",
    "backend.workers.style_helper",
]


async def generate_screens(job_id: str) -> bool:
    """
    Generate title and end screen videos for a job.
    
    This is the main entry point for the screens worker.
    Called automatically when both audio and lyrics processing complete.
    
    Args:
        job_id: Job ID to process
        
    Returns:
        True if successful, False otherwise
    """
    start_time = time.time()
    job_manager = JobManager()
    storage = StorageService()
    settings = get_settings()
    
    # Create job logger for remote debugging
    job_log = create_job_logger(job_id, "screens")
    
    # Log with structured markers for easy Cloud Logging queries
    logger.info(f"[job:{job_id}] WORKER_START worker=screens")
    
    # Set up log capture for VideoGenerator and style_helper
    log_handler = setup_job_logging(job_id, "screens", *SCREENS_WORKER_LOGGERS)
    
    job = job_manager.get_job(job_id)
    if not job:
        logger.error(f"[job:{job_id}] Job not found")
        return False
    
    # Validate both audio and lyrics are complete
    if not _validate_prerequisites(job):
        logger.error(f"[job:{job_id}] Prerequisites not met for screen generation")
        return False
    
    # Create temporary working directory
    temp_dir = tempfile.mkdtemp(prefix=f"karaoke_screens_{job_id}_")
    
    try:
        # Wrap entire worker in a tracing span
        with job_span("screens-worker", job_id, {"artist": job.artist, "title": job.title}) as root_span:
            # Use job_logging_context for proper log isolation when multiple jobs run concurrently
            with job_logging_context(job_id):
                job_log.info(f"Starting screen generation for {job.artist} - {job.title}")
                logger.info(f"[job:{job_id}] Starting screen generation for {job.artist} - {job.title}")
                
                # Transition to GENERATING_SCREENS state
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.GENERATING_SCREENS,
                    progress=50,
                    message="Generating title and end screens"
                )
                
                # Log style assets info
                style_assets = getattr(job, 'style_assets', {}) or {}
                job_log.info(f"Style assets from job: {list(style_assets.keys()) if style_assets else 'None'}")
                if style_assets:
                    for key, path in style_assets.items():
                        job_log.info(f"  {key}: {path}")
                
                # Load style configuration (downloads assets from GCS if available)
                with job_span("load-style-config", job_id):
                    job_log.info("Loading style configuration from GCS...")
                    style_config = await load_style_config(job, storage, temp_dir)
                    if style_config.has_custom_styles():
                        job_log.info("Using CUSTOM style configuration")
                        logger.info(f"[job:{job_id}] Using custom style configuration")
                        add_span_attribute("style_type", "custom")
                    else:
                        job_log.warning("Using DEFAULT style configuration (no custom styles found)")
                        logger.info(f"[job:{job_id}] Using default style configuration")
                        add_span_attribute("style_type", "default")
                
                # Initialize video generator
                video_generator = _create_video_generator(temp_dir)
                
                # Generate title screen with style config
                with job_span("generate-title-screen", job_id):
                    job_log.info("Generating title screen...")
                    title_screen_path = await _generate_title_screen(
                        job_id=job_id,
                        job=job,
                        video_generator=video_generator,
                        style_config=style_config,
                        temp_dir=temp_dir,
                        job_log=job_log
                    )
                    
                    if not title_screen_path:
                        raise Exception("Title screen generation failed")
                    job_log.info(f"Title screen generated: {title_screen_path}")
                
                # Generate end screen with style config
                with job_span("generate-end-screen", job_id):
                    job_log.info("Generating end screen...")
                    end_screen_path = await _generate_end_screen(
                        job_id=job_id,
                        job=job,
                        video_generator=video_generator,
                        style_config=style_config,
                        temp_dir=temp_dir,
                        job_log=job_log
                    )
                    
                    if not end_screen_path:
                        raise Exception("End screen generation failed")
                    job_log.info(f"End screen generated: {end_screen_path}")
                
                # Upload screens to GCS
                with job_span("upload-screens", job_id):
                    await _upload_screens(
                        job_id=job_id,
                        job_manager=job_manager,
                        storage=storage,
                        title_screen_path=title_screen_path,
                        end_screen_path=end_screen_path
                    )
                
                # Apply countdown padding if needed
                await _apply_countdown_padding_if_needed(job_id, job_manager, job)
                
                # Transition to AWAITING_REVIEW
                # Human must review lyrics before video can be rendered
                logger.info(f"[job:{job_id}] Screens generated, awaiting lyrics review")
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.AWAITING_REVIEW,
                    progress=55,
                    message="Ready for lyrics review. Please review and correct lyrics."
                )
                
                duration = time.time() - start_time
                root_span.set_attribute("duration_seconds", duration)
                logger.info(f"[job:{job_id}] WORKER_END worker=screens status=success duration={duration:.1f}s")

                # Mark screens progress as complete for idempotency
                # This allows the worker to be re-triggered after admin reset
                job_manager.update_state_data(job_id, 'screens_progress', {'stage': 'complete'})
                return True

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"[job:{job_id}] WORKER_END worker=screens status=error duration={duration:.1f}s error={e}")
        job_manager.mark_job_failed(
            job_id=job_id,
            error_message=f"Screen generation failed: {str(e)}",
            error_details={"stage": "screen_generation", "error": str(e)}
        )
        return False
        
    finally:
        # Remove log handler to avoid duplicate logging on future runs
        for logger_name in SCREENS_WORKER_LOGGERS:
            try:
                logging.getLogger(logger_name).removeHandler(log_handler)
            except Exception:
                pass
        
        # Cleanup temporary directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.debug(f"[job:{job_id}] Cleaned up temp directory: {temp_dir}")


def _validate_prerequisites(job) -> bool:
    """
    Validate that both audio and lyrics processing are complete.

    Single Responsibility: Validation logic separated from main flow.

    Args:
        job: Job object

    Returns:
        True if prerequisites met, False otherwise
    """
    # SAFETY NET: Enforce theme requirement at processing time
    # This catches any jobs that somehow bypassed JobManager.create_job() validation
    if not job.theme_id:
        logger.error(
            f"Job {job.job_id}: CRITICAL - No theme_id configured. "
            "All jobs must have a theme to generate styled videos. "
            "This job should have been rejected at creation time."
        )
        return False

    audio_complete = job.state_data.get('audio_complete', False)
    lyrics_complete = job.state_data.get('lyrics_complete', False)

    if not audio_complete:
        logger.error(f"Job {job.job_id}: Audio processing not complete")
        return False

    if not lyrics_complete:
        logger.error(f"Job {job.job_id}: Lyrics processing not complete")
        return False

    if not job.artist or not job.title:
        logger.error(f"Job {job.job_id}: Missing artist or title")
        return False
    
    return True


def _create_video_generator(temp_dir: str) -> VideoGenerator:
    """
    Create video generator with appropriate parameters.
    
    Dependency Inversion: Depends on VideoGenerator abstraction.
    
    Args:
        temp_dir: Temporary directory for output
        
    Returns:
        Configured VideoGenerator instance
    """
    # FFmpeg base command (same as in audio_worker)
    ffmpeg_base_command = "ffmpeg -hide_banner -loglevel error -nostats -y"
    
    return VideoGenerator(
        logger=logger,
        ffmpeg_base_command=ffmpeg_base_command,
        render_bounding_boxes=False,
        output_png=True,
        output_jpg=True
    )


async def _generate_title_screen(
    job_id: str,
    job,
    video_generator: VideoGenerator,
    style_config: StyleConfig,
    temp_dir: str,
    job_log = None
) -> Optional[str]:
    """
    Generate title screen video with custom style configuration.
    
    Single Responsibility: Only handles title screen generation.
    
    Args:
        job_id: Job ID
        job: Job object with artist/title
        video_generator: Video generator instance
        style_config: Style configuration with formats and assets
        temp_dir: Temporary directory
        job_log: Optional JobLogger for remote debugging
        
    Returns:
        Path to generated title screen, or None if failed
    """
    try:
        logger.info(f"Job {job_id}: Generating title screen")

        # Set up output paths
        # Sanitize artist/title to handle Unicode characters (curly quotes, em dashes, etc.)
        safe_artist = sanitize_filename(job.artist) if job.artist else "Unknown"
        safe_title = sanitize_filename(job.title) if job.title else "Unknown"
        artist_title = f"{safe_artist} - {safe_title}"
        output_image_filepath_noext = os.path.join(temp_dir, f"{artist_title} (Title)")
        output_video_filepath = os.path.join(temp_dir, f"{artist_title} (Title).mov")
        
        # Get title format settings from style config
        title_format = style_config.get_intro_format()
        intro_duration = style_config.intro_video_duration
        
        # Log detailed style info for debugging
        if job_log:
            job_log.info("Title screen format configuration:")
            job_log.info(f"  background_image: {title_format.get('background_image')}")
            job_log.info(f"  background_color: {title_format.get('background_color')}")
            job_log.info(f"  font: {title_format.get('font')}")
            job_log.info(f"  title_color: {title_format.get('title_color')}")
            job_log.info(f"  artist_color: {title_format.get('artist_color')}")
            job_log.info(f"  duration: {intro_duration}s")
            
            # Check if background image exists
            bg_image = title_format.get('background_image')
            if bg_image and os.path.exists(bg_image):
                job_log.info(f"  background_image file EXISTS: {os.path.getsize(bg_image)} bytes")
            elif bg_image:
                job_log.warning(f"  background_image file NOT FOUND: {bg_image}")
        
        logger.info(f"Job {job_id}: Title format - bg_image: {title_format.get('background_image')}, font: {title_format.get('font')}")
        
        # Generate title screen (synchronous method)
        video_generator.create_title_video(
            artist=job.artist,
            title=job.title,
            format=title_format,
            output_image_filepath_noext=output_image_filepath_noext,
            output_video_filepath=output_video_filepath,
            existing_title_image=title_format.get('existing_image'),
            intro_video_duration=intro_duration
        )
        
        if os.path.exists(output_video_filepath):
            logger.info(f"Job {job_id}: Title screen generated at {output_video_filepath}")
            return output_video_filepath
        else:
            logger.error(f"Job {job_id}: Title screen generation returned no file")
            return None
            
    except Exception as e:
        logger.error(f"Job {job_id}: Title screen generation error: {e}", exc_info=True)
        return None


async def _generate_end_screen(
    job_id: str,
    job,
    video_generator: VideoGenerator,
    style_config: StyleConfig,
    temp_dir: str,
    job_log = None
) -> Optional[str]:
    """
    Generate end screen video with custom style configuration.
    
    Single Responsibility: Only handles end screen generation.
    
    Args:
        job_id: Job ID
        job: Job object with artist/title
        video_generator: Video generator instance
        style_config: Style configuration with formats and assets
        temp_dir: Temporary directory
        job_log: Optional JobLogger for remote debugging
        
    Returns:
        Path to generated end screen, or None if failed
    """
    try:
        logger.info(f"Job {job_id}: Generating end screen")

        # Set up output paths
        # Sanitize artist/title to handle Unicode characters (curly quotes, em dashes, etc.)
        safe_artist = sanitize_filename(job.artist) if job.artist else "Unknown"
        safe_title = sanitize_filename(job.title) if job.title else "Unknown"
        artist_title = f"{safe_artist} - {safe_title}"
        output_image_filepath_noext = os.path.join(temp_dir, f"{artist_title} (End)")
        output_video_filepath = os.path.join(temp_dir, f"{artist_title} (End).mov")
        
        # Get end format settings from style config
        end_format = style_config.get_end_format()
        end_duration = style_config.end_video_duration
        
        # Log detailed style info for debugging
        if job_log:
            job_log.info("End screen format configuration:")
            job_log.info(f"  background_image: {end_format.get('background_image')}")
            job_log.info(f"  background_color: {end_format.get('background_color')}")
            job_log.info(f"  font: {end_format.get('font')}")
            job_log.info(f"  duration: {end_duration}s")
        
        logger.info(f"Job {job_id}: End format - bg_image: {end_format.get('background_image')}, font: {end_format.get('font')}")
        
        # Generate end screen (synchronous method)
        video_generator.create_end_video(
            artist=job.artist,
            title=job.title,
            format=end_format,
            output_image_filepath_noext=output_image_filepath_noext,
            output_video_filepath=output_video_filepath,
            existing_end_image=end_format.get('existing_image'),
            end_video_duration=end_duration
        )
        
        if os.path.exists(output_video_filepath):
            logger.info(f"Job {job_id}: End screen generated at {output_video_filepath}")
            return output_video_filepath
        else:
            logger.error(f"Job {job_id}: End screen generation returned no file")
            return None
            
    except Exception as e:
        logger.error(f"Job {job_id}: End screen generation error: {e}", exc_info=True)
        return None


async def _upload_screens(
    job_id: str,
    job_manager: JobManager,
    storage: StorageService,
    title_screen_path: str,
    end_screen_path: str
) -> None:
    """
    Upload title and end screens to GCS (video + images).
    
    Single Responsibility: Only handles uploads.
    
    VideoGenerator creates .mov, .jpg, and .png files when configured with
    output_png=True and output_jpg=True. We upload all three formats
    for feature parity with the local CLI.
    
    Args:
        job_id: Job ID
        job_manager: Job manager instance
        storage: Storage service instance
        title_screen_path: Path to title screen video (.mov)
        end_screen_path: Path to end screen video (.mov)
    """
    # Upload title screen video
    title_gcs_path = f"jobs/{job_id}/screens/title.mov"
    title_url = storage.upload_file(title_screen_path, title_gcs_path)
    job_manager.update_file_url(job_id, 'screens', 'title', title_url)
    logger.info(f"Job {job_id}: Uploaded title screen video")
    
    # Upload title screen images (.jpg and .png - created by VideoGenerator)
    title_base = title_screen_path.replace('.mov', '')
    for ext, key in [('.jpg', 'title_jpg'), ('.png', 'title_png')]:
        image_path = f"{title_base}{ext}"
        if os.path.exists(image_path):
            gcs_path = f"jobs/{job_id}/screens/title{ext}"
            url = storage.upload_file(image_path, gcs_path)
            job_manager.update_file_url(job_id, 'screens', key, url)
            logger.info(f"Job {job_id}: Uploaded title screen image ({ext})")
    
    # Upload end screen video
    end_gcs_path = f"jobs/{job_id}/screens/end.mov"
    end_url = storage.upload_file(end_screen_path, end_gcs_path)
    job_manager.update_file_url(job_id, 'screens', 'end', end_url)
    logger.info(f"Job {job_id}: Uploaded end screen video")
    
    # Upload end screen images (.jpg and .png - created by VideoGenerator)
    end_base = end_screen_path.replace('.mov', '')
    for ext, key in [('.jpg', 'end_jpg'), ('.png', 'end_png')]:
        image_path = f"{end_base}{ext}"
        if os.path.exists(image_path):
            gcs_path = f"jobs/{job_id}/screens/end{ext}"
            url = storage.upload_file(image_path, gcs_path)
            job_manager.update_file_url(job_id, 'screens', key, url)
            logger.info(f"Job {job_id}: Uploaded end screen image ({ext})")


async def _apply_countdown_padding_if_needed(
    job_id: str,
    job_manager: JobManager,
    job
) -> None:
    """
    Apply countdown padding to instrumentals if needed.
    
    If lyrics start very early (within 3 seconds), LyricsTranscriber
    adds a countdown intro ("3... 2... 1...") to the vocal audio.
    We need to pad the instrumentals to match.
    
    Single Responsibility: Only handles padding logic.
    
    Args:
        job_id: Job ID
        job_manager: Job manager instance
        job: Job object with lyrics metadata
    """
    # Check if countdown padding was added
    lyrics_metadata = job.state_data.get('lyrics_metadata', {})
    has_countdown = lyrics_metadata.get('has_countdown_padding', False)
    
    if has_countdown:
        logger.info(f"Job {job_id}: Countdown padding detected, applying to instrumentals")
        
        # Transition to APPLYING_PADDING state
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.APPLYING_PADDING,
            progress=52,
            message="Synchronizing countdown padding"
        )
        
        # TODO: Implement padding application
        # This requires:
        # 1. Download instrumental stems from GCS
        # 2. Add silence to beginning
        # 3. Re-upload padded versions
        # For now, we'll skip this and just transition back
        
        logger.info(f"Job {job_id}: Padding applied (TODO: implement actual padding)")
    else:
        logger.info(f"Job {job_id}: No countdown padding needed")

