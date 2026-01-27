"""
Review API routes - Combined lyrics + instrumental review.

These endpoints support the combined review flow where users review lyrics
AND select their instrumental in a single session.

The correction-data endpoint now includes instrumental options and backing
vocals analysis, and the complete endpoint requires instrumental selection.

Usage:
  Frontend URL: http://localhost:5173/?baseApiUrl=http://localhost:8000/api/review/{job_id}

The baseApiUrl includes the job_id, and all endpoints are relative to that.
"""
import asyncio
import logging
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, Set, Tuple

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.job_logging import job_log_context, JobLogger
from backend.services.tracing import create_span, add_span_attribute, add_span_event
from backend.services.encoding_service import get_encoding_service
from backend.api.dependencies import require_auth, require_review_auth
from backend.services.auth_service import UserType
from backend.config import get_settings

# LyricsTranscriber imports for preview generation
from karaoke_gen.lyrics_transcriber.types import CorrectionResult
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations

# Import from the unified style loader
from karaoke_gen.style_loader import load_styles_from_gcs


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/review", tags=["review"])

# Store job context for the session
# In production, this would be handled differently (e.g., session tokens)
_job_contexts: Dict[str, Dict[str, Any]] = {}

# Store preview video paths for serving
_preview_videos: Dict[str, Dict[str, str]] = {}

# Keep references to background tasks to prevent garbage collection
_background_tasks: Set[asyncio.Task] = set()


def _get_audio_hash(job_id: str) -> str:
    """Generate a consistent audio hash for a job."""
    return hashlib.md5(job_id.encode()).hexdigest()


@router.get("/{job_id}/ping")
async def ping(job_id: str):
    """Health check endpoint expected by frontend."""
    return {"status": "ok"}


@router.get("/{job_id}/correction-data")
async def get_correction_data(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Get correction data for the combined review interface.

    Returns the CorrectionResult data plus instrumental options and analysis
    for the combined lyrics + instrumental review UI.

    Response includes:
    - All lyrics correction data (segments, reference lyrics, anchors, etc.)
    - instrumental_options: List of available instrumental tracks with audio URLs
    - backing_vocals_analysis: Analysis data to help users choose instrumental
    """
    job_manager = JobManager()
    storage = StorageService()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not ready for review (current status: {job.status})"
        )

    # Check for updated corrections first (from previous review sessions where user edited lyrics)
    # This mirrors the logic in render_video_worker.py to ensure consistency
    corrections_updated_gcs = job.file_urls.get('lyrics', {}).get('corrections_updated')
    if not corrections_updated_gcs:
        # Try direct path for updated corrections
        corrections_updated_gcs = f"jobs/{job_id}/lyrics/corrections_updated.json"

    if corrections_updated_gcs and storage.file_exists(corrections_updated_gcs):
        corrections_gcs = corrections_updated_gcs
        logger.info(f"Job {job_id}: Using updated corrections from previous review")
    else:
        # Fall back to original corrections
        corrections_gcs = job.file_urls.get('lyrics', {}).get('corrections')
        if not corrections_gcs:
            corrections_gcs = f"jobs/{job_id}/lyrics/corrections.json"
            if not storage.file_exists(corrections_gcs):
                raise HTTPException(
                    status_code=404,
                    detail="Corrections data not found. Lyrics processing may not be complete."
                )

    # Download and return corrections data
    try:
        corrections_data = storage.download_json(corrections_gcs)

        # Add audio hash for the frontend
        audio_hash = _get_audio_hash(job_id)
        if 'metadata' not in corrections_data:
            corrections_data['metadata'] = {}
        corrections_data['metadata']['audio_hash'] = audio_hash
        corrections_data['metadata']['artist'] = job.artist
        corrections_data['metadata']['title'] = job.title

        # Store context for audio serving
        _job_contexts[job_id] = {
            'audio_hash': audio_hash,
            'audio_gcs_path': job.input_media_gcs_path
        }

        # === Add instrumental data for combined review ===

        # Get instrumental stem URLs
        stems = job.file_urls.get('stems', {})
        clean_url = stems.get('instrumental_clean')
        backing_url = stems.get('instrumental_with_backing')

        # Build instrumental options with signed URLs
        instrumental_options = []
        if clean_url:
            instrumental_options.append({
                "id": "clean",
                "label": "Clean Instrumental",
                "description": "No backing vocals - just the music",
                "audio_url": storage.generate_signed_url(clean_url, expiration_minutes=120),
            })
        if backing_url:
            instrumental_options.append({
                "id": "with_backing",
                "label": "Instrumental with Backing Vocals",
                "description": "Includes harmonies and background vocals",
                "audio_url": storage.generate_signed_url(backing_url, expiration_minutes=120),
            })

        corrections_data['instrumental_options'] = instrumental_options

        # Get backing vocals analysis from state_data (populated by screens_worker)
        backing_vocals_analysis = job.state_data.get('backing_vocals_analysis', {})
        corrections_data['backing_vocals_analysis'] = backing_vocals_analysis

        # Get waveform URL if available
        analysis_files = job.file_urls.get('analysis', {})
        waveform_url = analysis_files.get('backing_vocals_waveform')
        if waveform_url:
            corrections_data['backing_vocals_waveform_url'] = storage.generate_signed_url(
                waveform_url, expiration_minutes=120
            )

        # Transition to IN_REVIEW if not already
        if job.status == JobStatus.AWAITING_REVIEW:
            job_manager.transition_to_state(
                job_id=job_id,
                new_status=JobStatus.IN_REVIEW,
                message="User opened combined review interface"
            )

        logger.info(f"Job {job_id}: Serving correction data with instrumental options for combined review")
        return corrections_data

    except Exception as e:
        logger.error(f"Job {job_id}: Error loading corrections: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error loading corrections: {str(e)}")


@router.get("/{job_id}/audio/{audio_hash}")
async def get_audio_with_hash(
    job_id: str,
    audio_hash: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """Stream the audio file for playback (with hash parameter)."""
    return await _stream_audio(job_id)


@router.get("/{job_id}/audio/")
@router.get("/{job_id}/audio")
async def get_audio_no_hash(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """Stream the audio file for playback (without hash parameter)."""
    return await _stream_audio(job_id)


async def _stream_audio(job_id: str):
    """
    Stream the audio file for playback in the review interface.
    """
    job_manager = JobManager()
    storage = StorageService()
    
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    audio_gcs_path = job.input_media_gcs_path
    if not audio_gcs_path:
        raise HTTPException(status_code=404, detail="Audio file not found")
    
    # Download to temp file and stream
    try:
        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".flac") as tmp:
            tmp_path = tmp.name
        
        storage.download_file(audio_gcs_path, tmp_path)
        
        # Determine content type
        if audio_gcs_path.endswith('.flac'):
            media_type = "audio/flac"
        elif audio_gcs_path.endswith('.wav'):
            media_type = "audio/wav"
        elif audio_gcs_path.endswith('.mp3'):
            media_type = "audio/mpeg"
        else:
            media_type = "audio/mpeg"
        
        logger.info(f"Job {job_id}: Streaming audio for review")
        
        return FileResponse(
            tmp_path,
            media_type=media_type,
            filename=os.path.basename(audio_gcs_path),
            background=BackgroundTask(os.unlink, tmp_path),
        )
        
    except Exception as e:
        logger.error(f"Job {job_id}: Error streaming audio: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error streaming audio: {str(e)}")


@router.post("/{job_id}/complete")
async def complete_review(
    job_id: str,
    updated_data: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Complete the combined review - save corrected lyrics AND instrumental selection.

    This endpoint receives:
    - Updated correction data (lyrics corrections)
    - instrumental_selection: "clean" or "with_backing" (REQUIRED)

    After saving, triggers the render video worker which will use the
    pre-selected instrumental (no separate instrumental selection step).
    """
    job_manager = JobManager()
    storage = StorageService()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current status: {job.status})"
        )

    # === Require instrumental selection ===
    instrumental_selection = updated_data.get("instrumental_selection")
    if not instrumental_selection:
        raise HTTPException(
            status_code=400,
            detail="instrumental_selection is required. Must be 'clean' or 'with_backing'."
        )

    valid_selections = ["clean", "with_backing"]
    # Also allow "custom" for user-provided instrumentals
    if job.existing_instrumental_gcs_path:
        valid_selections.append("custom")

    if instrumental_selection not in valid_selections:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid instrumental_selection. Must be one of: {valid_selections}"
        )

    try:
        # Remove instrumental_selection from updated_data before saving to corrections
        # (it's stored separately in state_data)
        corrections_to_save = {k: v for k, v in updated_data.items() if k != "instrumental_selection"}

        # Save updated corrections to GCS
        corrections_gcs_path = f"jobs/{job_id}/lyrics/corrections_updated.json"
        storage.upload_json(corrections_gcs_path, corrections_to_save)
        job_manager.update_file_url(job_id, 'lyrics', 'corrections_updated', corrections_gcs_path)

        logger.info(f"Job {job_id}: Saved updated corrections")

        # === Store instrumental selection in state_data ===
        # This will be used by render_video_worker to skip AWAITING_INSTRUMENTAL_SELECTION
        job_manager.update_state_data(job_id, 'instrumental_selection', instrumental_selection)
        logger.info(f"Job {job_id}: Stored instrumental selection: {instrumental_selection}")

        # Clear worker progress keys to ensure workers will run fresh (not skip due to idempotency)
        # This handles cases where a job is re-reviewed after completion or where state was inconsistent.
        # The idempotency check in workers looks at {worker}_progress.stage == 'complete' to skip,
        # so we must clear these before triggering the render-video worker.
        progress_keys = ['render_progress', 'screens_progress', 'video_progress', 'encoding_progress']
        cleared = job_manager.delete_state_data_keys(job_id, progress_keys)
        if cleared:
            logger.info(f"Job {job_id}: Cleared worker progress keys for re-processing: {cleared}")

        # Transition to REVIEW_COMPLETE
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.REVIEW_COMPLETE,
            progress=70,
            message=f"Review complete (instrumental: {instrumental_selection}), rendering video"
        )

        # Trigger render video worker
        from backend.services.worker_service import get_worker_service
        worker_service = get_worker_service()

        # Run in background, keep reference to prevent garbage collection
        task = asyncio.create_task(worker_service.trigger_render_video_worker(job_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info(f"Job {job_id}: Combined review complete, triggered render video worker")

        return {
            "status": "success",
            "instrumental_selection": instrumental_selection,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job {job_id}: Error completing review: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error completing review: {str(e)}")


@router.post("/{job_id}/handlers")
async def update_handlers(
    job_id: str,
    enabled_handlers: list,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Update enabled correction handlers (optional feature).
    
    For now, just acknowledge the request - full handler support
    would require re-running correction.
    """
    logger.info(f"Job {job_id}: Handler update requested (not implemented)")
    return {"status": "success", "message": "Handler updates not yet implemented"}


@router.post("/{job_id}/add-lyrics")
async def add_lyrics(
    job_id: str,
    data: Dict[str, str],
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Add custom lyrics source and rerun correction.
    
    Uses the LyricsTranscriber's CorrectionOperations to add a new lyrics source
    and regenerate corrections with the new source included.
    """
    job_manager = JobManager()
    storage = StorageService()
    
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Job must be in review state to add lyrics
    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current status: {job.status})"
        )
    
    source = data.get("source", "").strip()
    lyrics_text = data.get("lyrics", "").strip()
    
    logger.info(f"Job {job_id}: Adding lyrics source '{source}' with {len(lyrics_text)} characters")
    
    # Use tracing and job_log_context for full observability
    with create_span("add-lyrics", {"job_id": job_id, "source": source, "lyrics_length": len(lyrics_text)}) as span:
        with job_log_context(job_id, worker="add-lyrics"):
            try:
                # Create temp directory for this operation
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Download current corrections.json
                    with create_span("download-corrections") as download_span:
                        corrections_gcs = f"jobs/{job_id}/lyrics/corrections.json"
                        corrections_path = os.path.join(temp_dir, "corrections.json")
                        storage.download_file(corrections_gcs, corrections_path)
                        download_span.set_attribute("gcs_path", corrections_gcs)
                    
                    with open(corrections_path, 'r', encoding='utf-8') as f:
                        original_data = json.load(f)
                    
                    # Load as CorrectionResult
                    correction_result = CorrectionResult.from_dict(original_data)
                    add_span_event("corrections_loaded", {
                        "segments": len(correction_result.corrected_segments) if correction_result.corrected_segments else 0,
                        "reference_sources": len(correction_result.reference_lyrics) if correction_result.reference_lyrics else 0,
                    })
                    
                    # Set up cache directory
                    cache_dir = os.path.join(temp_dir, "cache")
                    os.makedirs(cache_dir, exist_ok=True)
                    
                    # Add lyrics source using CorrectionOperations (this is the heavy operation)
                    with create_span("correction-operations-add-lyrics") as correction_span:
                        correction_span.set_attribute("source_name", source)
                        updated_result = CorrectionOperations.add_lyrics_source(
                            correction_result=correction_result,
                            source=source,
                            lyrics_text=lyrics_text,
                            cache_dir=cache_dir,
                            logger=logger
                        )
                        add_span_event("correction_complete", {
                            "new_segments": len(updated_result.corrected_segments) if updated_result.corrected_segments else 0,
                        })
                    
                    # Add audio hash for the frontend
                    audio_hash = _get_audio_hash(job_id)
                    if not updated_result.metadata:
                        updated_result.metadata = {}
                    updated_result.metadata['audio_hash'] = audio_hash
                    updated_result.metadata['artist'] = job.artist
                    updated_result.metadata['title'] = job.title
                    
                    # Upload updated corrections back to GCS
                    with create_span("upload-corrections") as upload_span:
                        updated_data = updated_result.to_dict()
                        storage.upload_json(corrections_gcs, updated_data)
                        upload_span.set_attribute("gcs_path", corrections_gcs)
                    
                    logger.info(f"Job {job_id}: Successfully added lyrics source '{source}'")
                    span.set_attribute("success", True)
                    
                    return {"status": "success", "data": updated_data}
                    
            except ValueError as e:
                # ValueError from CorrectionOperations (e.g., duplicate source name)
                logger.warning(f"Job {job_id}: Invalid add lyrics request: {e}")
                span.set_attribute("error", str(e))
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                logger.error(f"Job {job_id}: Failed to add lyrics: {e}", exc_info=True)
                span.set_attribute("error", str(e))
                raise HTTPException(status_code=500, detail=f"Failed to add lyrics: {str(e)}")


@router.post("/{job_id}/preview-video")
async def generate_preview_video(
    job_id: str,
    updated_data: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Generate a preview video with the current corrections.

    Uses the LyricsTranscriber's CorrectionOperations to generate a 360p preview
    video with the user's current corrections applied.

    When USE_GCE_PREVIEW_ENCODING is enabled, video encoding is offloaded to
    the high-performance GCE worker for faster generation (15-20s vs 60+s).
    """
    job_manager = JobManager()
    storage = StorageService()
    settings = get_settings()
    encoding_service = get_encoding_service()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Job must be in review state to generate preview
    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current status: {job.status})"
        )

    # Check if GCE preview encoding is enabled
    use_gce_preview = encoding_service.is_preview_enabled

    # Check if user wants theme background image (slower) or black background (faster, default)
    use_background_image = updated_data.get("use_background_image", False)
    logger.info(f"Job {job_id}: Generating preview video (GCE: {use_gce_preview}, background image: {use_background_image})")

    # Use tracing and job_log_context for full observability
    with create_span("generate-preview-video", {"job_id": job_id, "use_gce": use_gce_preview}) as span:
        with job_log_context(job_id, worker="preview"):
            try:
                # Create temp directory for this preview operation
                with tempfile.TemporaryDirectory() as temp_dir:
                    # 1. Download original corrections.json (has full structure)
                    with create_span("download-corrections-and-audio") as download_span:
                        corrections_gcs = f"jobs/{job_id}/lyrics/corrections.json"
                        corrections_path = os.path.join(temp_dir, "corrections.json")
                        storage.download_file(corrections_gcs, corrections_path)

                        with open(corrections_path, 'r', encoding='utf-8') as f:
                            original_data = json.load(f)

                        # 2. Download input audio
                        audio_path = os.path.join(temp_dir, "audio.flac")
                        storage.download_file(job.input_media_gcs_path, audio_path)
                        download_span.set_attribute("audio_gcs_path", job.input_media_gcs_path)

                    # 3. Load original as CorrectionResult
                    correction_result = CorrectionResult.from_dict(original_data)
                    add_span_event("corrections_loaded")

                    # 4. Get or create styles file for preview using unified style loader
                    with create_span("load-styles") as styles_span:
                        styles_path, _ = load_styles_from_gcs(
                            style_params_gcs_path=job.style_params_gcs_path,
                            style_assets=job.style_assets,
                            temp_dir=temp_dir,
                            download_func=storage.download_file,
                            logger=logger,
                        )
                        styles_span.set_attribute("styles_path", styles_path)

                    # 5. Set up output config for preview
                    output_dir = os.path.join(temp_dir, "output")
                    cache_dir = os.path.join(temp_dir, "cache")
                    os.makedirs(output_dir, exist_ok=True)
                    os.makedirs(cache_dir, exist_ok=True)

                    output_config = OutputConfig(
                        output_styles_json=styles_path,
                        output_dir=output_dir,
                        cache_dir=cache_dir,
                        video_resolution="360p",
                    )

                    # 6. Generate preview (ASS-only if using GCE, or full video if local)
                    preview_gcs_path = None

                    if use_gce_preview:
                        # GCE path: Generate ASS only, then offload encoding to GCE
                        try:
                            with create_span("generate-ass-subtitles") as ass_span:
                                result = CorrectionOperations.generate_preview_video(
                                    correction_result=correction_result,
                                    updated_data=updated_data,
                                    output_config=output_config,
                                    audio_filepath=audio_path,
                                    artist=job.artist,
                                    title=job.title,
                                    logger=logger,
                                    ass_only=True,  # Only generate ASS, skip video encoding
                                )
                                preview_hash = result["preview_hash"]
                                ass_path = result["ass_path"]
                                ass_span.set_attribute("ass_path", ass_path)
                                add_span_event("ass_generated")

                            # Upload ASS to GCS
                            with create_span("upload-ass-to-gcs") as upload_ass_span:
                                ass_gcs_path = f"jobs/{job_id}/previews/{preview_hash}.ass"
                                storage.upload_file(ass_path, ass_gcs_path)
                                upload_ass_span.set_attribute("ass_gcs_path", ass_gcs_path)

                            # Call GCE encoding service
                            with create_span("gce-preview-encoding") as gce_span:
                                bucket_name = settings.gcs_bucket_name
                                preview_gcs_path = f"jobs/{job_id}/previews/{preview_hash}.mp4"

                                # Get background image and font from style assets if available
                                style_assets = job.style_assets or {}

                                # Only use background image if user explicitly requested it
                                # Default is black background for faster preview generation (~10s vs ~30-60s)
                                background_image_gcs_path = None
                                if use_background_image:
                                    for key in ["karaoke_background", "style_karaoke_background"]:
                                        if key in style_assets:
                                            background_image_gcs_path = f"gs://{bucket_name}/{style_assets[key]}"
                                            gce_span.set_attribute("background_image", background_image_gcs_path)
                                            break
                                gce_span.set_attribute("use_background_image", use_background_image)

                                font_gcs_path = None
                                for key in ["font", "style_font"]:
                                    if key in style_assets:
                                        font_gcs_path = f"gs://{bucket_name}/{style_assets[key]}"
                                        gce_span.set_attribute("font", font_gcs_path)
                                        break

                                gce_result = await encoding_service.encode_preview_video(
                                    job_id=f"preview_{job_id}_{preview_hash}",
                                    ass_gcs_path=f"gs://{bucket_name}/{ass_gcs_path}",
                                    audio_gcs_path=f"gs://{bucket_name}/{job.input_media_gcs_path}",
                                    output_gcs_path=f"gs://{bucket_name}/{preview_gcs_path}",
                                    background_color="black",
                                    background_image_gcs_path=background_image_gcs_path,
                                    font_gcs_path=font_gcs_path,
                                )
                                gce_span.set_attribute("gce_status", gce_result.get("status"))
                                add_span_event("gce_encoding_complete")

                            logger.info(f"Job {job_id}: Preview generated via GCE: {preview_hash}")

                        except Exception as gce_error:
                            # Fall back to local encoding if GCE fails
                            logger.warning(
                                f"Job {job_id}: GCE preview encoding failed, falling back to local: {gce_error}"
                            )
                            span.set_attribute("gce_fallback", True)
                            use_gce_preview = False  # Fall through to local encoding below

                    if not use_gce_preview:
                        # Local path: Generate full preview video locally
                        with create_span("render-preview-video-local") as render_span:
                            render_span.set_attribute("resolution", "360p")
                            result = CorrectionOperations.generate_preview_video(
                                correction_result=correction_result,
                                updated_data=updated_data,
                                output_config=output_config,
                                audio_filepath=audio_path,
                                artist=job.artist,
                                title=job.title,
                                logger=logger,
                                ass_only=False,  # Generate full video locally
                            )
                            preview_hash = result["preview_hash"]
                            video_path = result["video_path"]
                            add_span_event("render_complete")

                        # Upload preview video to GCS
                        with create_span("upload-preview-video") as upload_span:
                            preview_gcs_path = f"jobs/{job_id}/previews/{preview_hash}.mp4"
                            storage.upload_file(video_path, preview_gcs_path)
                            upload_span.set_attribute("gcs_path", preview_gcs_path)

                    # Store the GCS path for serving
                    if job_id not in _preview_videos:
                        _preview_videos[job_id] = {}
                    _preview_videos[job_id][preview_hash] = preview_gcs_path

                    logger.info(f"Job {job_id}: Preview video generated: {preview_hash}")
                    span.set_attribute("preview_hash", preview_hash)
                    span.set_attribute("success", True)

                    return {"status": "success", "preview_hash": preview_hash}

            except Exception as e:
                logger.error(f"Job {job_id}: Failed to generate preview video: {e}", exc_info=True)
                span.set_attribute("error", str(e))
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to generate preview video: {e}"
                ) from e


@router.get("/{job_id}/preview-video/{preview_hash}")
async def get_preview_video(
    job_id: str,
    preview_hash: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """Stream the generated preview video."""
    storage = StorageService()
    
    # Check in-memory cache first
    preview_gcs_path = None
    if job_id in _preview_videos and preview_hash in _preview_videos[job_id]:
        preview_gcs_path = _preview_videos[job_id][preview_hash]
    else:
        # Try standard path
        preview_gcs_path = f"jobs/{job_id}/previews/{preview_hash}.mp4"
        if not storage.file_exists(preview_gcs_path):
            raise HTTPException(status_code=404, detail="Preview video not found")
    
    try:
        # Download to temp file and stream
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp_path = tmp.name
        
        storage.download_file(preview_gcs_path, tmp_path)
        
        logger.info(f"Job {job_id}: Streaming preview video {preview_hash}")
        
        return FileResponse(
            tmp_path,
            media_type="video/mp4",
            filename=f"preview_{preview_hash}.mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Disposition": "inline",
                "Cache-Control": "no-cache",
            },
            background=BackgroundTask(os.unlink, tmp_path),
        )
        
    except Exception as e:
        logger.error(f"Job {job_id}: Error streaming preview video: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error streaming preview video: {str(e)}")


@router.post("/{job_id}/v1/annotations")
async def submit_annotation(
    job_id: str,
    annotation: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Submit a correction annotation for ML training data.
    
    For now, just log and acknowledge - full annotation support
    would require a database.
    """
    logger.info(f"Job {job_id}: Annotation submitted (logged but not stored)")
    return {"status": "success", "annotation_id": "stub"}


@router.get("/{job_id}/v1/annotations/stats")
async def get_annotation_stats(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """Get annotation statistics."""
    return {
        "total_annotations": 0,
        "by_type": {},
        "message": "Annotation stats not yet implemented"
    }


@router.get("/{job_id}/instrumental-analysis")
async def get_instrumental_analysis(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Get instrumental analysis data for the review interface.

    Returns analysis data in the format expected by InstrumentalSelector component:
    - analysis: backing vocals analysis with audible segments
    - audio_urls: signed URLs for instrumental stems
    - has_original: whether original audio is available
    """
    job_manager = JobManager()
    storage = StorageService()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get backing vocals analysis from state_data
    backing_analysis = job.state_data.get('backing_vocals_analysis', {})

    # Get stem URLs
    stems = job.file_urls.get('stems', {})
    clean_url = stems.get('instrumental_clean')
    backing_url = stems.get('instrumental_with_backing')

    # Build audio URLs with signed access
    audio_urls = {}
    if clean_url:
        audio_urls['clean'] = storage.generate_signed_url(clean_url, expiration_minutes=120)
    if backing_url:
        audio_urls['with_backing'] = storage.generate_signed_url(backing_url, expiration_minutes=120)
    if job.input_media_gcs_path:
        audio_urls['original'] = storage.generate_signed_url(job.input_media_gcs_path, expiration_minutes=120)

    # Check for backing vocals stem for playback
    backing_vocals_path = stems.get('backing_vocals')
    if backing_vocals_path:
        audio_urls['backing_vocals'] = storage.generate_signed_url(backing_vocals_path, expiration_minutes=120)

    # Format response to match InstrumentalAnalysis type
    return {
        "job_id": job_id,
        "artist": job.artist,
        "title": job.title,
        "duration_seconds": backing_analysis.get('total_duration_seconds'),
        "analysis": {
            "has_audible_content": backing_analysis.get('has_audible_content', False),
            "total_duration_seconds": backing_analysis.get('total_duration_seconds', 0),
            "audible_segments": backing_analysis.get('audible_segments', []),
            "recommended_selection": backing_analysis.get('recommended_selection', 'with_backing'),
            "total_audible_duration_seconds": backing_analysis.get('total_audible_duration_seconds', 0),
            "audible_percentage": backing_analysis.get('audible_percentage', 0),
            "silence_threshold_db": backing_analysis.get('silence_threshold_db', -40),
        },
        "audio_urls": audio_urls,
        "has_original": bool(job.input_media_gcs_path),
        "has_uploaded_instrumental": bool(job.existing_instrumental_gcs_path),
    }


@router.get("/{job_id}/waveform-data")
async def get_waveform_data(
    job_id: str,
    num_points: int = 1000,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Get waveform amplitude data for client-side rendering.

    Returns amplitude values for drawing the waveform in the frontend.
    """
    from backend.services.audio_analysis_service import AudioAnalysisService

    job_manager = JobManager()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get backing vocals path
    stems = job.file_urls.get('stems', {})
    backing_vocals_path = stems.get('backing_vocals')

    if not backing_vocals_path:
        # Fallback to input audio if no backing vocals
        backing_vocals_path = job.input_media_gcs_path

    if not backing_vocals_path:
        raise HTTPException(status_code=404, detail="No audio available for waveform")

    try:
        analysis_service = AudioAnalysisService()
        amplitudes, duration = analysis_service.get_waveform_data(
            gcs_audio_path=backing_vocals_path,
            job_id=job_id,
            num_points=num_points,
        )

        return {
            "amplitudes": amplitudes,
            "duration_seconds": duration,
            "duration": duration,  # Legacy field name
            "sample_rate": num_points,
        }
    except Exception as e:
        logger.error(f"Job {job_id}: Error generating waveform data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating waveform: {str(e)}")
