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
from typing import Dict, Any, List, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from backend.models.job import JobStatus
from backend.models.review_session import ReviewSession, ReviewSessionSummary
from backend.services.job_manager import JobManager
from backend.services.firestore_service import FirestoreService
from backend.services.storage_service import StorageService
from backend.services.job_logging import job_log_context, JobLogger
from backend.services.tracing import create_span, add_span_attribute, add_span_event
from backend.services.encoding_service import get_encoding_service
from backend.api.dependencies import require_auth, require_review_auth
from backend.services.auth_service import UserType
from backend.config import get_settings
from backend.i18n import t, get_locale_from_request

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


# Cross-job search endpoint MUST be registered before {job_id} routes
# to avoid FastAPI matching "sessions" as a job_id path parameter.
@router.get("/sessions/search")
async def search_review_sessions(
    q: str = "",
    limit: int = 20,
    auth_info: Tuple[str, str] = Depends(require_auth)
):
    """
    Search review sessions across all jobs (admin only).

    Query params:
    - q: Search text (matches artist, title, or job_id)
    - limit: Max results (default 20)
    """
    firestore_svc = FirestoreService()
    results = firestore_svc.search_review_sessions(query_text=q, limit=limit)

    return {"sessions": results}


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
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.reviewNotReady", status=job.status)
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
                    detail=t("en", "review.correctionsNotFound")
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
        from backend.services.audio_transcoding_service import AudioTranscodingService
        transcoding = AudioTranscodingService(storage_service=storage)

        # Get instrumental stem URLs
        stems = job.file_urls.get('stems', {})
        clean_url = stems.get('instrumental_clean')
        backing_url = stems.get('instrumental_with_backing')

        # Build instrumental options with transcoded signed URLs (OGG Opus)
        instrumental_options = []
        url_tasks = {}
        if clean_url:
            url_tasks['clean'] = transcoding.get_review_audio_url_async(clean_url, expiration_minutes=120)
        if backing_url:
            url_tasks['with_backing'] = transcoding.get_review_audio_url_async(backing_url, expiration_minutes=120)

        signed_urls = {}
        if url_tasks:
            results = await asyncio.gather(*url_tasks.values())
            signed_urls = dict(zip(url_tasks.keys(), results))

        if clean_url:
            instrumental_options.append({
                "id": "clean",
                "label": "Clean Instrumental",
                "description": "No backing vocals - just the music",
                "audio_url": signed_urls['clean'],
            })
        if backing_url:
            instrumental_options.append({
                "id": "with_backing",
                "label": "Instrumental with Backing Vocals",
                "description": "Includes harmonies and background vocals",
                "audio_url": signed_urls['with_backing'],
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
        raise HTTPException(status_code=500, detail=t("en", "review.correctionsLoadFailed", error=str(e)))


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
    Redirect to a signed GCS URL for audio playback in the review interface.

    Serves transcoded OGG Opus (~3 MB) instead of raw FLAC (~35 MB).
    Falls back to FLAC if transcoded version is not available.
    """
    from backend.services.audio_transcoding_service import AudioTranscodingService

    job_manager = JobManager()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    audio_gcs_path = job.input_media_gcs_path
    if not audio_gcs_path:
        raise HTTPException(status_code=404, detail=t("en", "review.audioNotFound"))

    try:
        transcoding = AudioTranscodingService()
        signed_url = await transcoding.get_review_audio_url_async(audio_gcs_path, expiration_minutes=120)
        logger.info(f"Job {job_id}: Redirecting to signed URL for audio review")
        return RedirectResponse(url=signed_url, status_code=302)

    except Exception as e:
        logger.error(f"Job {job_id}: Error generating audio signed URL: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=t("en", "review.audioServeError", error=str(e)))


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
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.reviewNotReady", status=job.status)
        )

    # === Require instrumental selection ===
    instrumental_selection = updated_data.get("instrumental_selection")
    if not instrumental_selection:
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.instrumentalSelectionRequired")
        )

    valid_selections = ["clean", "with_backing"]
    # Allow "custom" for mute-region instrumentals or user-uploaded instrumentals
    stems = job.file_urls.get("stems", {}) if job.file_urls else {}
    if job.existing_instrumental_gcs_path or stems.get("custom_instrumental"):
        valid_selections.append("custom")

    if instrumental_selection not in valid_selections:
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.invalidInstrumentalSelection", valid_selections=valid_selections)
        )

    # === is_duet flag (optional) — latch-up-only ===
    # InstrumentalSelector may send is_duet=False on the complete call when
    # correctionData.is_duet is undefined (the flag doesn't round-trip through
    # the /corrections response on the cloud path). Never downgrade True→False:
    # if a prior submit_corrections already persisted True, honor that.
    is_duet_raw = updated_data.get("is_duet", None)
    if is_duet_raw is not None and not isinstance(is_duet_raw, bool):
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.invalidIsDuetFlag"),
        )

    try:
        # Remove instrumental_selection and is_duet from updated_data before saving corrections
        # (they're stored separately in state_data)
        excluded_fields = {"instrumental_selection", "is_duet"}
        corrections_to_save = {k: v for k, v in updated_data.items() if k not in excluded_fields}

        # Only write corrections_updated.json if there's actual correction data.
        # Writing an empty dict causes KeyError downstream when render workers
        # try to access updated_data["corrections"].
        if corrections_to_save and "corrections" in corrections_to_save:
            corrections_gcs_path = f"jobs/{job_id}/lyrics/corrections_updated.json"
            storage.upload_json(corrections_gcs_path, corrections_to_save)
            job_manager.update_file_url(job_id, 'lyrics', 'corrections_updated', corrections_gcs_path)
            logger.info(f"Job {job_id}: Saved updated corrections")
        else:
            logger.info(f"Job {job_id}: No corrections data in request, skipping corrections_updated.json")

        # === Store instrumental selection in state_data ===
        # This will be used by render_video_worker to skip AWAITING_INSTRUMENTAL_SELECTION
        job_manager.update_state_data(job_id, 'instrumental_selection', instrumental_selection)
        logger.info(f"Job {job_id}: Stored instrumental selection: {instrumental_selection}")

        # Store duet flag so render worker knows to use multi-singer styles.
        # Latch-up-only: True overwrites; False is ignored if a prior submit
        # already persisted True.
        if is_duet_raw is True:
            job_manager.update_state_data(job_id, 'is_duet', True)
            logger.info(f"Job {job_id}: Stored is_duet flag: True (from complete)")
        elif is_duet_raw is False:
            job = job_manager.get_job(job_id)
            prior = bool(job.state_data.get('is_duet', False)) if job else False
            if prior:
                logger.info(
                    f"Job {job_id}: Ignoring is_duet=False on complete — already True from prior submit"
                )
            else:
                job_manager.update_state_data(job_id, 'is_duet', False)
                logger.info(f"Job {job_id}: Stored is_duet flag: False")
        # else is_duet_raw is None: leave existing state_data value untouched

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
        raise HTTPException(status_code=500, detail=t("en", "review.reviewCompletedError", error=str(e)))


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
    return {"status": "success", "message": t("en", "review.handlersUpdateNotImplemented")}


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
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    # Job must be in review state to add lyrics
    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.reviewNotReady", status=job.status)
        )
    
    source = data.get("source", "").strip()
    lyrics_text = data.get("lyrics", "").strip()
    raw_force = data.get("force", False)
    force = raw_force if isinstance(raw_force, bool) else str(raw_force).lower() == "true"

    logger.info(f"Job {job_id}: Adding lyrics source '{source}' with {len(lyrics_text)} characters (force={force})")
    
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
                            logger=logger,
                            force=force,
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
                raise HTTPException(status_code=400, detail=t("en", "review.lyricsAddInvalidRequest", error=str(e)))
            except Exception as e:
                logger.error(f"Job {job_id}: Failed to add lyrics: {e}", exc_info=True)
                span.set_attribute("error", str(e))
                raise HTTPException(status_code=500, detail=t("en", "review.lyricsAddError", error=str(e)))


@router.post("/{job_id}/search-lyrics")
async def search_lyrics(
    job_id: str,
    data: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Search for lyrics using an alternate artist/title and rerun correction.

    Queries all configured lyrics providers (Genius, Spotify, Musixmatch, LRCLIB)
    with the supplied artist and title, then runs the correction pipeline (which
    includes the relevance filter). Sources that pass the filter are added to the
    job's corrections.json and returned to the frontend.

    Request body:
        artist (str): Artist name to search.
        title (str): Song title to search.
        force_sources (list[str], optional): Provider names whose results should
            bypass the relevance filter.

    Response:
        200 {"status": "success", "data": {...CorrectionData...}, "sources_added": [...],
             "sources_rejected": {...}, "sources_not_found": [...]}
        200 {"status": "no_results", "message": "...", "sources_rejected": {...},
             "sources_not_found": [...]}
        400 on invalid request / wrong job state
        500 on internal error
    """
    job_manager = JobManager()
    storage = StorageService()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.reviewNotReady", status=job.status)
        )

    artist = (data.get("artist") or "").strip()
    title = (data.get("title") or "").strip()
    force_sources: List[str] = data.get("force_sources") or []

    if not artist or not title:
        raise HTTPException(status_code=400, detail="artist and title are required")

    logger.info(f"Job {job_id}: Searching lyrics for '{artist}' - '{title}' (force={force_sources})")

    with create_span("search-lyrics", {"job_id": job_id, "artist": artist, "title": title}) as span:
        with job_log_context(job_id, worker="search-lyrics"):
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Download current corrections.json
                    with create_span("download-corrections") as download_span:
                        corrections_gcs = f"jobs/{job_id}/lyrics/corrections.json"
                        corrections_path = os.path.join(temp_dir, "corrections.json")
                        storage.download_file(corrections_gcs, corrections_path)
                        download_span.set_attribute("gcs_path", corrections_gcs)

                    with open(corrections_path, "r", encoding="utf-8") as f:
                        original_data = json.load(f)

                    correction_result = CorrectionResult.from_dict(original_data)
                    add_span_event("corrections_loaded", {
                        "segments": len(correction_result.corrected_segments) if correction_result.corrected_segments else 0,
                        "reference_sources": len(correction_result.reference_lyrics) if correction_result.reference_lyrics else 0,
                    })

                    cache_dir = os.path.join(temp_dir, "cache")
                    os.makedirs(cache_dir, exist_ok=True)

                    with create_span("correction-operations-search-lyrics") as correction_span:
                        correction_span.set_attribute("artist", artist)
                        correction_span.set_attribute("title", title)
                        search_result = CorrectionOperations.search_lyrics_sources(
                            correction_result=correction_result,
                            artist=artist,
                            title=title,
                            cache_dir=cache_dir,
                            force_sources=force_sources,
                            logger=logger,
                        )

                    sources_added = search_result["sources_added"]
                    sources_rejected = search_result["sources_rejected"]
                    sources_not_found = search_result["sources_not_found"]
                    updated_result = search_result["updated_result"]

                    if not sources_added or updated_result is None:
                        logger.info(
                            f"Job {job_id}: No valid lyrics found via search "
                            f"(rejected={list(sources_rejected.keys())}, not_found={sources_not_found})"
                        )
                        span.set_attribute("success", False)
                        return {
                            "status": "no_results",
                            "message": t("en", "review.lyricsSearchNoResults"),
                            "sources_added": [],
                            "sources_rejected": sources_rejected,
                            "sources_not_found": sources_not_found,
                        }

                    # Add audio hash / job metadata for the frontend
                    audio_hash = _get_audio_hash(job_id)
                    if not updated_result.metadata:
                        updated_result.metadata = {}
                    updated_result.metadata["audio_hash"] = audio_hash
                    updated_result.metadata["artist"] = job.artist
                    updated_result.metadata["title"] = job.title

                    # Upload updated corrections back to GCS
                    with create_span("upload-corrections") as upload_span:
                        updated_data = updated_result.to_dict()
                        storage.upload_json(corrections_gcs, updated_data)
                        upload_span.set_attribute("gcs_path", corrections_gcs)

                    logger.info(
                        f"Job {job_id}: Search complete — added={sources_added}, "
                        f"rejected={list(sources_rejected.keys())}, not_found={sources_not_found}"
                    )
                    span.set_attribute("success", True)
                    span.set_attribute("sources_added", len(sources_added))

                    return {
                        "status": "success",
                        "data": updated_data,
                        "sources_added": sources_added,
                        "sources_rejected": sources_rejected,
                        "sources_not_found": sources_not_found,
                    }

            except HTTPException:
                raise
            except ValueError as e:
                logger.warning(f"Job {job_id}: Invalid search lyrics request: {e}")
                span.set_attribute("error", str(e))
                raise HTTPException(status_code=400, detail=t("en", "review.lyricsSearchInvalidRequest", error=str(e)))
            except Exception as e:
                logger.error(f"Job {job_id}: Failed to search lyrics: {e}", exc_info=True)
                span.set_attribute("error", str(e))
                raise HTTPException(status_code=500, detail=t("en", "review.lyricsSearchError", error=str(e)))


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
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    # Job must be in review state to generate preview
    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.reviewNotReady", status=job.status)
        )

    # Check if GCE preview encoding is enabled
    use_gce_preview = encoding_service.is_preview_enabled

    # Check if user wants theme background image (slower) or black background (faster, default)
    use_background_image = updated_data.get("use_background_image", False)

    # === is_duet flag (optional, defaults to False) ===
    is_duet_raw = updated_data.get("is_duet", False)
    if not isinstance(is_duet_raw, bool):
        raise HTTPException(
            status_code=400,
            detail=t("en", "review.invalidIsDuetFlag"),
        )
    is_duet = is_duet_raw

    logger.info(f"Job {job_id}: Generating preview video (GCE: {use_gce_preview}, background image: {use_background_image}, is_duet: {is_duet})")

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
                        is_duet=is_duet,
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
                    detail=t("en", "review.previewVideoGenerationError", error=str(e))
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
            raise HTTPException(status_code=404, detail=t("en", "review.previewVideoNotFound"))
    
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
        raise HTTPException(status_code=500, detail=t("en", "review.previewVideoStreamError", error=str(e)))


@router.post("/{job_id}/v1/annotations")
async def submit_annotation(
    job_id: str,
    payload: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Submit correction annotations for ML training data.

    Accepts either a single annotation dict or {"annotations": [...]} batch.
    Stores to GCS at jobs/{job_id}/lyrics/annotations.json.
    """
    annotations = payload.get("annotations", [payload]) if isinstance(payload, dict) else [payload]

    try:
        storage = StorageService()
        gcs_path = f"jobs/{job_id}/lyrics/annotations.json"

        # Merge with any existing annotations
        existing: list = []
        try:
            existing_data = storage.download_json(gcs_path)
            if isinstance(existing_data, list):
                existing = existing_data
            elif isinstance(existing_data, dict) and "annotations" in existing_data:
                existing = existing_data["annotations"]
        except Exception:
            pass  # No existing file, start fresh

        merged = existing + annotations
        storage.upload_json(gcs_path, {"annotations": merged})

        logger.info(f"Job {job_id}: {len(annotations)} annotation(s) saved ({len(merged)} total)")
        return {"status": "success", "saved_count": len(annotations), "total_count": len(merged)}

    except Exception as e:
        logger.error(f"Job {job_id}: Error saving annotations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=t("en", "review.annotationSaveError", error=str(e)))


@router.get("/{job_id}/v1/annotations/stats")
async def get_annotation_stats(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """Get annotation statistics."""
    try:
        storage = StorageService()
        gcs_path = f"jobs/{job_id}/lyrics/annotations.json"
        data = storage.download_json(gcs_path)
        annotations = data.get("annotations", []) if isinstance(data, dict) else data
        by_type: Dict[str, int] = {}
        for a in annotations:
            atype = a.get("annotation_type", "unknown")
            by_type[atype] = by_type.get(atype, 0) + 1
        return {"total_annotations": len(annotations), "by_type": by_type}
    except Exception:
        return {"total_annotations": 0, "by_type": {}}


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
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    # Get backing vocals analysis from state_data
    backing_analysis = job.state_data.get('backing_vocals_analysis', {})

    # Get stem URLs
    from backend.services.audio_transcoding_service import AudioTranscodingService
    transcoding = AudioTranscodingService(storage_service=storage)

    stems = job.file_urls.get('stems', {})
    clean_url = stems.get('instrumental_clean')
    backing_url = stems.get('instrumental_with_backing')
    backing_vocals_path = stems.get('backing_vocals')

    # Build audio URLs with transcoded signed access (OGG Opus, parallel)
    url_tasks = {}
    if clean_url:
        url_tasks['clean'] = transcoding.get_review_audio_url_async(clean_url, expiration_minutes=120)
    if backing_url:
        url_tasks['with_backing'] = transcoding.get_review_audio_url_async(backing_url, expiration_minutes=120)
    if job.input_media_gcs_path:
        url_tasks['original'] = transcoding.get_review_audio_url_async(job.input_media_gcs_path, expiration_minutes=120)
    if backing_vocals_path:
        url_tasks['backing_vocals'] = transcoding.get_review_audio_url_async(backing_vocals_path, expiration_minutes=120)

    audio_urls = {}
    if url_tasks:
        results = await asyncio.gather(*url_tasks.values())
        audio_urls = dict(zip(url_tasks.keys(), results))

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
            "recommended_selection": backing_analysis.get('recommended_selection', 'clean'),
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
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    # Get backing vocals path
    stems = job.file_urls.get('stems', {})
    backing_vocals_path = stems.get('backing_vocals')

    if not backing_vocals_path:
        # Fallback to input audio if no backing vocals
        backing_vocals_path = job.input_media_gcs_path

    if not backing_vocals_path:
        raise HTTPException(status_code=404, detail=t("en", "review.waveformNoAudio"))

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
        raise HTTPException(status_code=500, detail=t("en", "review.waveformGenerationError", error=str(e)))


# ============================================
# Review Session Endpoints (backup/restore)
# ============================================


@router.post("/{job_id}/sessions")
async def save_review_session(
    job_id: str,
    data: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Save a review session snapshot for backup/restore.

    Stores correction data in GCS and metadata in Firestore subcollection.
    Skips save if the data is identical to the most recent session (deduplication).

    Request body:
    - correction_data: Full CorrectionData object
    - edit_count: Number of edits in this session
    - trigger: "auto" | "preview" | "manual"
    - summary: { total_segments, total_words, corrections_made, changed_words[] }
    """
    job_manager = JobManager()
    firestore_svc = FirestoreService()
    storage = StorageService()

    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    correction_data = data.get("correction_data")
    if not correction_data:
        raise HTTPException(status_code=400, detail=t("en", "review.sessionSaveError"))

    # Compute hash for deduplication
    data_json = json.dumps(correction_data, sort_keys=True, default=str)
    data_hash = hashlib.sha256(data_json.encode()).hexdigest()

    # Check if identical to most recent session
    latest_hash = firestore_svc.get_latest_review_session_hash(job_id)
    if latest_hash == data_hash:
        logger.info(f"Job {job_id}: Skipping duplicate review session save")
        return {"status": "skipped", "reason": "identical_data"}

    # Get audio duration from backing_vocals_analysis if available
    audio_duration = None
    backing_analysis = job.state_data.get("backing_vocals_analysis", {})
    if backing_analysis:
        audio_duration = backing_analysis.get("total_duration_seconds")

    # Build session
    user_email = auth_info[0] if auth_info else ""
    summary_data = data.get("summary", {})

    session = ReviewSession(
        job_id=job_id,
        user_email=user_email,
        edit_count=data.get("edit_count", 0),
        trigger=data.get("trigger", "auto"),
        audio_duration_seconds=audio_duration,
        artist=job.artist,
        title=job.title,
        summary=ReviewSessionSummary.from_dict(summary_data),
        data_hash=data_hash,
    )

    # Upload correction data to GCS
    gcs_path = f"jobs/{job_id}/review_sessions/{session.session_id}.json"
    storage.upload_json(gcs_path, correction_data)
    session.correction_data_gcs_path = gcs_path

    # Save metadata to Firestore
    firestore_svc.save_review_session(job_id, session)

    logger.info(f"Job {job_id}: Saved review session {session.session_id} (trigger={session.trigger}, edits={session.edit_count})")
    return {
        "status": "saved",
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
    }


@router.get("/{job_id}/sessions")
async def list_review_sessions(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    List all review sessions for a job (metadata only, no correction_data).
    Ordered by updated_at descending (most recent first).
    """
    firestore_svc = FirestoreService()
    sessions = firestore_svc.list_review_sessions(job_id)

    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "job_id": s.job_id,
                "user_email": s.user_email,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                "edit_count": s.edit_count,
                "trigger": s.trigger,
                "audio_duration_seconds": s.audio_duration_seconds,
                "artist": s.artist,
                "title": s.title,
                "summary": s.summary.to_dict(),
            }
            for s in sessions
        ]
    }


@router.get("/{job_id}/sessions/{session_id}")
async def get_review_session(
    job_id: str,
    session_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """
    Get a single review session with full correction_data loaded from GCS.
    """
    firestore_svc = FirestoreService()
    storage = StorageService()

    session = firestore_svc.get_review_session(job_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    # Load correction data from GCS
    correction_data = None
    if session.correction_data_gcs_path:
        try:
            correction_data = storage.download_json(session.correction_data_gcs_path)
        except Exception as e:
            logger.error(f"Job {job_id}: Error loading correction data for session {session_id}: {e}")
            raise HTTPException(status_code=500, detail="Error loading session correction data")

    return {
        "session_id": session.session_id,
        "job_id": session.job_id,
        "user_email": session.user_email,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "edit_count": session.edit_count,
        "trigger": session.trigger,
        "audio_duration_seconds": session.audio_duration_seconds,
        "artist": session.artist,
        "title": session.title,
        "summary": session.summary.to_dict(),
        "correction_data": correction_data,
    }


@router.delete("/{job_id}/sessions/{session_id}")
async def delete_review_session(
    job_id: str,
    session_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth)
):
    """Delete a review session (metadata + GCS data)."""
    firestore_svc = FirestoreService()
    storage = StorageService()

    session = firestore_svc.get_review_session(job_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    # Delete GCS data
    if session.correction_data_gcs_path:
        try:
            storage.delete_file(session.correction_data_gcs_path)
        except Exception:
            logger.warning(f"Job {job_id}: Could not delete GCS file for session {session_id}")

    # Delete Firestore doc
    firestore_svc.delete_review_session(job_id, session_id)

    return {"status": "deleted", "session_id": session_id}


# ============================================
# Audio Edit Session Endpoints (backup/restore)
# ============================================


@router.post("/{job_id}/audio-edit-sessions")
async def save_audio_edit_session(
    job_id: str,
    data: Dict[str, Any],
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """Save an audio edit session snapshot."""
    import hashlib
    import json as json_mod
    from backend.models.audio_edit_session import AudioEditSession, AudioEditSessionSummary

    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    user_email = auth_info[0] if isinstance(auth_info, tuple) else ""
    edit_data = data.get("edit_data", {})

    # Compute hash for deduplication
    data_json = json_mod.dumps(edit_data, sort_keys=True, default=str)
    data_hash = hashlib.sha256(data_json.encode()).hexdigest()

    # Skip if identical to most recent
    firestore_svc = FirestoreService()
    latest_hash = firestore_svc.get_latest_audio_edit_session_hash(job_id)
    if latest_hash == data_hash:
        return {"status": "skipped", "reason": "identical_data"}

    # Build session
    summary_data = data.get("summary", {})
    session = AudioEditSession(
        job_id=job_id,
        user_email=user_email,
        edit_count=data.get("edit_count", 0),
        trigger=data.get("trigger", "auto"),
        audio_duration_seconds=summary_data.get("net_duration_seconds"),
        original_duration_seconds=data.get("original_duration_seconds"),
        artist=job.artist,
        title=job.title,
        summary=AudioEditSessionSummary.from_dict(summary_data),
        data_hash=data_hash,
    )

    # Upload edit_data to GCS
    storage = StorageService()
    gcs_path = f"jobs/{job_id}/audio_edit_sessions/{session.session_id}.json"
    storage.upload_json(gcs_path, edit_data)
    session.edit_data_gcs_path = gcs_path

    # Save metadata to Firestore
    firestore_svc.save_audio_edit_session(job_id, session)

    return {
        "status": "saved",
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
    }


@router.get("/{job_id}/audio-edit-sessions")
async def list_audio_edit_sessions(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """List all audio edit sessions for a job (metadata only)."""
    firestore_svc = FirestoreService()
    sessions = firestore_svc.list_audio_edit_sessions(job_id)
    return {
        "sessions": [s.to_dict() for s in sessions]
    }


@router.get("/{job_id}/audio-edit-sessions/{session_id}")
async def get_audio_edit_session(
    job_id: str,
    session_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """Get a single audio edit session with full edit_data loaded from GCS."""
    firestore_svc = FirestoreService()
    session = firestore_svc.get_audio_edit_session(job_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = session.to_dict()

    # Load edit data from GCS
    if session.edit_data_gcs_path:
        try:
            storage = StorageService()
            result["edit_data"] = storage.download_json(session.edit_data_gcs_path)
        except Exception as e:
            logger.warning(f"Could not load audio edit data from GCS for session {session_id}: {e}")
            result["edit_data"] = None
    else:
        result["edit_data"] = None

    return result


@router.delete("/{job_id}/audio-edit-sessions/{session_id}")
async def delete_audio_edit_session(
    job_id: str,
    session_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """Delete an audio edit session (metadata + GCS data)."""
    firestore_svc = FirestoreService()
    session = firestore_svc.get_audio_edit_session(job_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete GCS data
    if session.edit_data_gcs_path:
        try:
            storage = StorageService()
            storage.delete_file(session.edit_data_gcs_path)
        except Exception:
            logger.warning(f"Could not delete GCS file for audio edit session {session_id}")

    # Delete Firestore doc
    firestore_svc.delete_audio_edit_session(job_id, session_id)

    return {"status": "deleted", "session_id": session_id}


# ============================================
# Audio Edit Endpoints
# ============================================


def _get_current_audio_gcs_path(job) -> str:
    """Get the GCS path of the current audio (latest edit or original)."""
    state_data = job.state_data or {}
    edit_stack = state_data.get('audio_edit_stack', [])
    if edit_stack:
        return edit_stack[-1]['gcs_path']
    return job.input_media_gcs_path


def _build_audio_edit_response(
    job_id: str,
    gcs_path: str,
    edit_stack: list,
    redo_stack: list,
    edit_id: str | None = None,
) -> dict:
    """Build a standard response for audio edit operations matching AudioEditResponse interface."""
    from backend.services.audio_analysis_service import AudioAnalysisService
    from backend.services.audio_transcoding_service import AudioTranscodingService

    analysis_service = AudioAnalysisService()
    transcoding_service = AudioTranscodingService()

    # Get waveform data for current audio
    amplitudes, duration = analysis_service.get_waveform_data(
        gcs_audio_path=gcs_path,
        job_id=job_id,
        num_points=1000,
    )

    # Get playback URL (OGG Opus)
    playback_url = transcoding_service.get_review_audio_url(gcs_path)

    # Build flat response matching frontend AudioEditResponse interface
    response = {
        "status": "success",
        "duration_after": duration,
        "current_audio_url": playback_url,
        "waveform_data": {"amplitudes": list(amplitudes)},
        "edit_stack": [
            {
                "edit_id": entry.get("edit_id", ""),
                "operation": entry.get("operation", ""),
                "params": entry.get("params", {}),
                "duration_before": entry.get("duration_before", 0),
                "duration_after": entry.get("duration_after", entry.get("duration_seconds", 0)),
                "timestamp": entry.get("timestamp", ""),
            }
            for entry in edit_stack
        ],
        "can_undo": len(edit_stack) > 0,
        "can_redo": len(redo_stack) > 0,
    }
    if edit_id is not None:
        response["edit_id"] = edit_id
    return response


@router.get("/{job_id}/input-audio-info")
async def get_input_audio_info(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """
    Get metadata about the input audio for the audio editor UI.

    Returns duration, waveform data, playback URL, and edit state.
    """
    from backend.services.audio_analysis_service import AudioAnalysisService
    from backend.services.audio_transcoding_service import AudioTranscodingService
    from backend.services.audio_edit_service import AudioEditService

    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if not job.input_media_gcs_path:
        raise HTTPException(status_code=404, detail="No input audio available")

    try:
        edit_service = AudioEditService()
        analysis_service = AudioAnalysisService()
        transcoding_service = AudioTranscodingService()

        state_data = job.state_data or {}
        edit_stack = state_data.get('audio_edit_stack', [])

        # Try cached waveform first (pre-generated by download worker), fall back to on-demand
        waveform_cache_path = state_data.get('audio_edit_waveform_cache_path')
        cached = None
        if waveform_cache_path:
            cached = analysis_service.load_cached_waveform(waveform_cache_path)

        if cached:
            orig_amplitudes, orig_duration = cached
            logger.info(f"Job {job_id}: Using cached waveform data")
        else:
            orig_amplitudes, orig_duration = analysis_service.get_waveform_data(
                gcs_audio_path=job.input_media_gcs_path,
                job_id=job_id,
                num_points=1000,
            )

        original_playback_url = transcoding_service.get_review_audio_url(job.input_media_gcs_path)

        # Determine current audio state
        current_duration = orig_duration
        current_audio_url = original_playback_url
        current_waveform = orig_amplitudes
        if edit_stack:
            current_gcs_path = edit_stack[-1]['gcs_path']
            current_waveform, current_duration = analysis_service.get_waveform_data(
                gcs_audio_path=current_gcs_path,
                job_id=job_id,
                num_points=1000,
            )
            current_audio_url = transcoding_service.get_review_audio_url(current_gcs_path)

        # Return flat structure matching AudioEditInfo frontend interface
        result = {
            "job_id": job_id,
            "artist": job.artist,
            "title": job.title,
            "original_duration_seconds": orig_duration,
            "current_duration_seconds": current_duration,
            "original_audio_url": original_playback_url,
            "current_audio_url": current_audio_url,
            "waveform_data": {"amplitudes": list(current_waveform)},
            "original_waveform_data": {"amplitudes": list(orig_amplitudes)},
            "edit_stack": [
                {
                    "edit_id": entry.get("edit_id", ""),
                    "operation": entry.get("operation", ""),
                    "params": entry.get("params", {}),
                    "duration_before": entry.get("duration_before", 0),
                    "duration_after": entry.get("duration_after", 0),
                    "timestamp": entry.get("timestamp", ""),
                }
                for entry in edit_stack
            ],
            "can_undo": len(edit_stack) > 0,
            "can_redo": len(state_data.get('audio_edit_redo_stack', [])) > 0,
        }

        return result

    except Exception as e:
        logger.error(f"Job {job_id}: Error getting input audio info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting audio info: {str(e)}")


@router.post("/{job_id}/audio-edit/apply")
async def apply_audio_edit(
    job_id: str,
    request: Request,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """
    Apply an edit operation to the input audio.

    Supported operations: trim_start, trim_end, cut, mute, join_start, join_end.
    Returns updated waveform data and playback URL.
    """
    import uuid
    from backend.services.audio_edit_service import AudioEditService

    body = await request.json()
    operation = body.get("operation")
    params = body.get("params", {})
    edit_id = body.get("edit_id") or str(uuid.uuid4())

    if not operation:
        raise HTTPException(status_code=400, detail="Missing 'operation' field")

    valid_operations = {"trim_start", "trim_end", "cut", "mute", "join_start", "join_end"}
    if operation not in valid_operations:
        raise HTTPException(status_code=400, detail=f"Invalid operation: {operation}. Valid: {valid_operations}")

    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in (JobStatus.AWAITING_AUDIO_EDIT, JobStatus.IN_AUDIO_EDIT):
        raise HTTPException(status_code=400, detail=f"Job is not in audio edit state (current: {job.status})")

    # Transition to IN_AUDIO_EDIT if not already
    if job.status == JobStatus.AWAITING_AUDIO_EDIT:
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.IN_AUDIO_EDIT,
            progress=15,
            message="Audio editing in progress"
        )

    # Resolve upload_id to GCS path for join operations
    if operation in ("join_start", "join_end"):
        upload_id = params.get("upload_id")
        if not upload_id:
            raise HTTPException(status_code=400, detail="Missing 'upload_id' for join operation")
        state_data = job.state_data or {}
        uploads = state_data.get('audio_edit_uploads', {})
        upload_info = uploads.get(upload_id)
        if not upload_info:
            raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found")
        params["upload_gcs_path"] = upload_info["gcs_path"]

    try:
        edit_service = AudioEditService()

        # Get current audio path and duration before edit
        state_data = job.state_data or {}
        existing_stack = state_data.get('audio_edit_stack', [])
        current_gcs_path = _get_current_audio_gcs_path(job)

        # Determine duration before this edit
        if existing_stack:
            duration_before = existing_stack[-1].get('duration_seconds', 0)
        else:
            before_metadata = edit_service.get_metadata_from_gcs(current_gcs_path)
            duration_before = before_metadata.duration_seconds

        output_gcs_path = f"jobs/{job_id}/audio_edit/edit_{edit_id}.flac"

        # Apply the edit
        metadata, result_path = edit_service.apply_edit(
            input_gcs_path=current_gcs_path,
            operation=operation,
            params=params,
            output_gcs_path=output_gcs_path,
            job_id=job_id,
        )

        # Update edit stack in state_data
        edit_stack = list(existing_stack)
        edit_stack.append({
            "edit_id": edit_id,
            "operation": operation,
            "params": {k: v for k, v in params.items() if k != "upload_gcs_path"},
            "gcs_path": result_path,
            "duration_before": duration_before,
            "duration_after": metadata.duration_seconds,
            "duration_seconds": metadata.duration_seconds,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        })

        # Clear redo stack on new edit
        updated_state_data = {
            **state_data,
            'audio_edit_stack': edit_stack,
            'audio_edit_redo_stack': [],
        }
        job_manager.update_job(job_id, {'state_data': updated_state_data})

        return _build_audio_edit_response(
            job_id=job_id,
            gcs_path=result_path,
            edit_stack=edit_stack,
            redo_stack=[],
            edit_id=edit_id,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Job {job_id}: Error applying audio edit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error applying edit: {str(e)}")


@router.post("/{job_id}/audio-edit/undo")
async def undo_audio_edit(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """Undo the last audio edit operation."""
    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in (JobStatus.AWAITING_AUDIO_EDIT, JobStatus.IN_AUDIO_EDIT):
        raise HTTPException(status_code=400, detail=f"Job is not in audio edit state (current: {job.status})")

    state_data = job.state_data or {}
    edit_stack = list(state_data.get('audio_edit_stack', []))
    redo_stack = list(state_data.get('audio_edit_redo_stack', []))

    if not edit_stack:
        raise HTTPException(status_code=400, detail="Nothing to undo")

    # Pop from edit stack, push to redo stack
    undone_edit = edit_stack.pop()
    redo_stack.append(undone_edit)

    updated_state_data = {
        **state_data,
        'audio_edit_stack': edit_stack,
        'audio_edit_redo_stack': redo_stack,
    }
    job_manager.update_job(job_id, {'state_data': updated_state_data})

    # Get the now-current audio (previous edit or original)
    current_gcs_path = edit_stack[-1]['gcs_path'] if edit_stack else job.input_media_gcs_path

    try:
        return _build_audio_edit_response(
            job_id=job_id,
            gcs_path=current_gcs_path,
            edit_stack=edit_stack,
            redo_stack=redo_stack,
        )
    except Exception as e:
        logger.error(f"Job {job_id}: Error building undo response: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during undo: {str(e)}")


@router.post("/{job_id}/audio-edit/redo")
async def redo_audio_edit(
    job_id: str,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """Redo a previously undone audio edit."""
    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in (JobStatus.AWAITING_AUDIO_EDIT, JobStatus.IN_AUDIO_EDIT):
        raise HTTPException(status_code=400, detail=f"Job is not in audio edit state (current: {job.status})")

    state_data = job.state_data or {}
    edit_stack = list(state_data.get('audio_edit_stack', []))
    redo_stack = list(state_data.get('audio_edit_redo_stack', []))

    if not redo_stack:
        raise HTTPException(status_code=400, detail="Nothing to redo")

    # Pop from redo stack, push to edit stack
    redone_edit = redo_stack.pop()
    edit_stack.append(redone_edit)

    updated_state_data = {
        **state_data,
        'audio_edit_stack': edit_stack,
        'audio_edit_redo_stack': redo_stack,
    }
    job_manager.update_job(job_id, {'state_data': updated_state_data})

    current_gcs_path = edit_stack[-1]['gcs_path']

    try:
        return _build_audio_edit_response(
            job_id=job_id,
            gcs_path=current_gcs_path,
            edit_stack=edit_stack,
            redo_stack=redo_stack,
        )
    except Exception as e:
        logger.error(f"Job {job_id}: Error building redo response: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during redo: {str(e)}")


@router.post("/{job_id}/audio-edit/upload")
async def upload_audio_for_join(
    job_id: str,
    request: Request,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """
    Upload additional audio for join operations.

    Accepts multipart form upload. Returns upload_id and metadata.
    """
    import uuid
    from fastapi import UploadFile
    from backend.services.audio_edit_service import AudioEditService
    from backend.services.audio_analysis_service import AudioAnalysisService

    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in (JobStatus.AWAITING_AUDIO_EDIT, JobStatus.IN_AUDIO_EDIT):
        raise HTTPException(status_code=400, detail=f"Job is not in audio edit state (current: {job.status})")

    # Parse multipart form
    form = await request.form()
    file = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="Missing 'file' in form data")

    # Validate file extension
    filename = getattr(file, 'filename', 'unknown')
    valid_extensions = {'.flac', '.wav', '.mp3', '.m4a', '.ogg'}
    ext = os.path.splitext(filename)[1].lower()
    if ext not in valid_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{ext}'. Accepted: {', '.join(valid_extensions)}"
        )

    upload_id = str(uuid.uuid4())
    gcs_path = f"jobs/{job_id}/audio_edit/upload_{upload_id}{ext}"

    try:
        storage = StorageService()
        edit_service = AudioEditService()
        analysis_service = AudioAnalysisService()

        # Save uploaded file to GCS
        content = await file.read()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            storage.upload_file(tmp_path, gcs_path)

            # Get metadata
            metadata = edit_service.get_metadata(tmp_path)

            # Get waveform data
            from karaoke_gen.instrumental_review import WaveformGenerator
            waveform_gen = WaveformGenerator()
            amplitudes, duration = waveform_gen.generate_data_only(
                audio_path=tmp_path,
                num_points=1000,
            )
        finally:
            os.unlink(tmp_path)

        # Store upload info in state_data
        state_data = job.state_data or {}
        uploads = dict(state_data.get('audio_edit_uploads', {}))
        uploads[upload_id] = {
            "gcs_path": gcs_path,
            "duration_seconds": metadata.duration_seconds,
            "filename": filename,
        }
        updated_state_data = {**state_data, 'audio_edit_uploads': uploads}
        job_manager.update_job(job_id, {'state_data': updated_state_data})

        return {
            "upload_id": upload_id,
            "duration_seconds": metadata.duration_seconds,
            "waveform_data": {
                "amplitudes": amplitudes,
                "duration": duration,
            },
        }

    except Exception as e:
        logger.error(f"Job {job_id}: Error uploading audio for join: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error uploading audio: {str(e)}")


@router.post("/{job_id}/audio-edit/submit")
async def submit_audio_edit(
    job_id: str,
    request: Request,
    auth_info: Tuple[str, str] = Depends(require_review_auth),
):
    """
    Finalize the audio edit and continue processing.

    Copies the current edited audio to the job's input path,
    then triggers audio separation and lyrics transcription workers.
    """
    from backend.services.worker_service import get_worker_service

    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=t("en", "review.jobNotFound"))

    if job.status not in (JobStatus.AWAITING_AUDIO_EDIT, JobStatus.IN_AUDIO_EDIT):
        raise HTTPException(status_code=400, detail=f"Job is not in audio edit state (current: {job.status})")

    state_data = job.state_data or {}
    edit_stack = state_data.get('audio_edit_stack', [])

    storage = StorageService()

    # Save edit log if provided
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    edit_log = body.get("edit_log")
    if edit_log:
        edit_log_path = f"jobs/{job_id}/audio_edit/edit_log.json"
        storage.upload_json(edit_log_path, edit_log)
        logger.info(f"Job {job_id}: Saved audio edit log to {edit_log_path}")

    if edit_stack:
        # Copy the latest edited audio to the canonical input path
        current_edited_path = edit_stack[-1]['gcs_path']
        edited_input_path = f"jobs/{job_id}/input/edited.flac"

        # Download and re-upload to canonical path
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = os.path.join(temp_dir, "edited.flac")
            storage.download_file(current_edited_path, local_path)
            storage.upload_file(local_path, edited_input_path)

        # Update job to use edited audio as input
        job_manager.update_job(job_id, {
            'input_media_gcs_path': edited_input_path,
        })
        logger.info(f"Job {job_id}: Audio edit submitted, using edited audio: {edited_input_path}")
    else:
        logger.info(f"Job {job_id}: Audio edit submitted with no edits, using original audio")

    # Transition state
    job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.AUDIO_EDIT_COMPLETE,
        progress=18,
        message="Audio edit complete, starting processing"
    )

    # Trigger parallel workers in background so the HTTP response returns immediately.
    # The worker triggers involve Cloud Run Job calls that can take 30+ seconds;
    # waiting synchronously causes the browser request to time out.
    worker_service = get_worker_service()

    async def _trigger_workers():
        try:
            await asyncio.gather(
                worker_service.trigger_audio_worker(job_id),
                worker_service.trigger_lyrics_worker(job_id),
            )
        except Exception as e:
            logger.error(f"Job {job_id}: Failed to trigger workers after audio edit: {e}")

    asyncio.create_task(_trigger_workers())

    return {
        "status": "success",
        "message": "Audio edit saved. Processing will continue with edited audio.",
        "job_id": job_id,
        "edits_applied": len(edit_stack),
    }
