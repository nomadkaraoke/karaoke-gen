"""
Audio search API routes for artist+title search mode.

This module provides endpoints for:
1. Creating a job with audio search (artist+title without file)
2. Getting search results for a job
3. Selecting an audio source to download

The flow is:
1. POST /api/audio-search/search - Create job and search for audio
2. GET /api/audio-search/{job_id}/results - Get search results
3. POST /api/audio-search/{job_id}/select - Select audio source
"""
import asyncio
import logging
import mimetypes
import os
import shutil
import tempfile
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends
from pydantic import BaseModel, Field, validator

from backend.models.job import JobCreate, JobStatus
from karaoke_gen.utils import normalize_text
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.worker_service import get_worker_service
from backend.services.credential_manager import get_credential_manager, CredentialStatus
from backend.services.audio_search_service import (
    get_audio_search_service,
    AudioSearchResult,
    AudioSearchError,
    NoResultsError,
    DownloadError,
)
from backend.services.youtube_download_service import (
    get_youtube_download_service,
    YouTubeDownloadError,
)
from backend.services.theme_service import get_theme_service
from backend.services.job_defaults_service import resolve_cdg_txt_defaults
from backend.config import get_settings
from backend.version import VERSION
from backend.api.dependencies import require_auth
from backend.services.auth_service import UserType, AuthResult
from backend.middleware.tenant import get_tenant_config_from_request
from pathlib import Path

logger = logging.getLogger(__name__)

# Valid style file types (from file_upload.py)
STYLE_FILE_TYPES = {
    'style_params': {'.json'},
    'style_intro_background': {'.jpg', '.jpeg', '.png'},
    'style_karaoke_background': {'.jpg', '.jpeg', '.png'},
    'style_end_background': {'.jpg', '.jpeg', '.png'},
    'style_font': {'.ttf', '.otf'},
    'style_cdg_instrumental_background': {'.jpg', '.jpeg', '.png'},
    'style_cdg_title_background': {'.jpg', '.jpeg', '.png'},
    'style_cdg_outro_background': {'.jpg', '.jpeg', '.png'},
}
router = APIRouter(tags=["audio-search"])

# Initialize services
job_manager = JobManager()
storage_service = StorageService()
worker_service = get_worker_service()


# ============================================================================
# Pydantic models
# ============================================================================

class StyleFileRequest(BaseModel):
    """Information about a style file to be uploaded."""
    filename: str = Field(..., description="Original filename with extension")
    content_type: str = Field(..., description="MIME type of the file")
    file_type: str = Field(..., description="Type of file: 'style_params', 'style_intro_background', 'style_karaoke_background', 'style_end_background', 'style_font', 'style_cdg_instrumental_background', 'style_cdg_title_background', 'style_cdg_outro_background'")


class StyleUploadUrl(BaseModel):
    """Signed URL for uploading a style file."""
    file_type: str = Field(..., description="Type of file being uploaded")
    gcs_path: str = Field(..., description="Destination path in GCS")
    upload_url: str = Field(..., description="Signed URL to PUT the file to")


class AudioSearchRequest(BaseModel):
    """Request to search for audio by artist and title."""
    artist: str = Field(..., description="Artist name to search for")
    title: str = Field(..., description="Song title to search for")

    # Auto-download mode
    auto_download: bool = Field(False, description="Automatically select best result and download")

    # Theme configuration
    theme_id: Optional[str] = Field(None, description="Theme ID to use (e.g., 'nomad', 'default'). If set, CDG/TXT are enabled by default.")
    color_overrides: Optional[Dict[str, str]] = Field(None, description="Color overrides: artist_color, title_color, sung_lyrics_color, unsung_lyrics_color (hex #RRGGBB)")

    # Processing options (CDG/TXT require style config or theme, disabled by default unless theme is set)
    enable_cdg: Optional[bool] = Field(None, description="Generate CDG+MP3 package. Default: True if theme_id set, False otherwise")
    enable_txt: Optional[bool] = Field(None, description="Generate TXT+MP3 package. Default: True if theme_id set, False otherwise")
    
    # Finalisation options
    brand_prefix: Optional[str] = Field(None, description="Brand code prefix (e.g., NOMAD)")
    enable_youtube_upload: Optional[bool] = Field(None, description="Upload to YouTube. None = use server default")
    youtube_description: Optional[str] = Field(None, description="YouTube video description text")
    discord_webhook_url: Optional[str] = Field(None, description="Discord webhook URL for notifications")
    
    # Distribution options
    dropbox_path: Optional[str] = Field(None, description="Dropbox folder path for organized output")
    gdrive_folder_id: Optional[str] = Field(None, description="Google Drive folder ID for public share uploads")
    
    # Lyrics configuration
    lyrics_artist: Optional[str] = Field(None, description="Override artist name for lyrics search")
    lyrics_title: Optional[str] = Field(None, description="Override title for lyrics search")
    subtitle_offset_ms: int = Field(0, description="Subtitle timing offset in milliseconds")

    # Display overrides (optional - if empty, search values are used for display)
    display_artist: Optional[str] = Field(None, description="Artist name for title screens/filenames. If empty, uses search artist.")
    display_title: Optional[str] = Field(None, description="Title for title screens/filenames. If empty, uses search title.")
    
    # Audio separation model configuration
    clean_instrumental_model: Optional[str] = Field(None, description="Model for clean instrumental separation")
    backing_vocals_models: Optional[List[str]] = Field(None, description="Models for backing vocals separation")
    other_stems_models: Optional[List[str]] = Field(None, description="Models for other stems")
    
    # Style file uploads (optional)
    # Style params JSON and related assets (background images, fonts)
    style_files: Optional[List[StyleFileRequest]] = Field(None, description="Style files to upload (style_params JSON, backgrounds, fonts)")

    # Non-interactive mode
    non_interactive: bool = Field(False, description="Skip interactive steps (lyrics review, instrumental selection)")

    @validator('artist', 'title', 'lyrics_artist', 'lyrics_title', 'display_artist', 'display_title')
    def normalize_text_fields(cls, v):
        """Normalize text fields to standardize Unicode characters."""
        if v is not None and isinstance(v, str):
            return normalize_text(v)
        return v


class AudioSearchResultResponse(BaseModel):
    """A single audio search result.
    
    Contains all fields needed for rich display using flacfetch's
    shared formatting functions.
    """
    index: int
    title: str
    artist: str
    provider: str  # Maps to source_name in Release
    url: Optional[str] = None  # Maps to download_url in Release (may be None for remote)
    duration: Optional[int] = None  # Maps to duration_seconds in Release
    quality: Optional[str] = None  # Stringified quality
    source_id: Optional[str] = None  # Maps to info_hash in Release
    seeders: Optional[int] = None
    target_file: Optional[str] = None
    # Additional fields for rich display (from Release.to_dict())
    year: Optional[int] = None
    label: Optional[str] = None
    edition_info: Optional[str] = None
    release_type: Optional[str] = None
    channel: Optional[str] = None  # For YouTube
    view_count: Optional[int] = None  # For YouTube
    size_bytes: Optional[int] = None
    target_file_size: Optional[int] = None
    track_pattern: Optional[str] = None
    match_score: Optional[float] = None
    # Pre-computed display fields
    formatted_size: Optional[str] = None
    formatted_duration: Optional[str] = None
    formatted_views: Optional[str] = None
    is_lossless: Optional[bool] = None
    quality_str: Optional[str] = None
    # Full quality object for Release.from_dict()
    quality_data: Optional[Dict[str, Any]] = None


class AudioSearchResponse(BaseModel):
    """Response from audio search."""
    status: str
    job_id: str
    message: str
    results: Optional[List[AudioSearchResultResponse]] = None
    results_count: int = 0
    auto_download: bool = False
    server_version: str
    # Style file upload URLs (when style_files are specified in request)
    style_upload_urls: Optional[List[StyleUploadUrl]] = None


class AudioSelectRequest(BaseModel):
    """Request to select an audio source."""
    selection_index: int = Field(..., description="Index of the selected audio source from search results")


class AudioSelectResponse(BaseModel):
    """Response from audio source selection."""
    status: str
    job_id: str
    message: str
    selected_index: int
    selected_title: str
    selected_artist: str
    selected_provider: str


def extract_request_metadata(request: Request, created_from: str = "audio_search") -> Dict[str, Any]:
    """Extract metadata from request for job tracking."""
    headers = dict(request.headers)
    
    client_ip = headers.get('x-forwarded-for', '').split(',')[0].strip()
    if not client_ip and request.client:
        client_ip = request.client.host
    
    user_agent = headers.get('user-agent', '')
    environment = headers.get('x-environment', '')
    client_id = headers.get('x-client-id', '')
    
    custom_headers = {}
    for key, value in headers.items():
        if key.lower().startswith('x-') and key.lower() not in ('x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host'):
            custom_headers[key] = value
    
    return {
        'client_ip': client_ip,
        'user_agent': user_agent,
        'environment': environment,
        'client_id': client_id,
        'server_version': VERSION,
        'created_from': created_from,
        'custom_headers': custom_headers,
    }


async def _trigger_workers_parallel(job_id: str) -> None:
    """Trigger both audio and lyrics workers in parallel."""
    await asyncio.gather(
        worker_service.trigger_audio_worker(job_id),
        worker_service.trigger_lyrics_worker(job_id)
    )


def _extract_gcs_path(filepath: str) -> str:
    """
    Extract the path portion from a GCS path or gs:// URL.

    Args:
        filepath: Either a gs:// URL or a plain path

    Returns:
        The path portion (e.g., "uploads/job123/audio/file.flac")
    """
    if filepath.startswith("gs://"):
        # Format: gs://bucket/path/to/file
        parts = filepath.replace("gs://", "").split("/", 1)
        if len(parts) == 2:
            return parts[1]
    return filepath


async def _download_and_start_processing(
    job_id: str,
    selection_index: int,
    audio_search_service,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Download selected audio and start job processing.
    
    This is called either:
    - Immediately after search if auto_download=True
    - When user calls the select endpoint
    
    For remote flacfetch downloads (torrent sources), the file is downloaded
    on the flacfetch VM and uploaded directly to GCS. For local downloads
    (YouTube), the file is downloaded locally and then uploaded to GCS.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    # Get search results from state_data
    search_results = job.state_data.get('audio_search_results', [])
    if not search_results:
        raise HTTPException(status_code=400, detail="No search results available")

    if selection_index < 0 or selection_index >= len(search_results):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid selection index {selection_index}. Valid range: 0-{len(search_results)-1}"
        )

    selected = search_results[selection_index]

    # Get remote_search_id from state_data (stored during initial search)
    remote_search_id = job.state_data.get('remote_search_id')
    
    # Transition to downloading state
    job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.DOWNLOADING_AUDIO,
        progress=10,
        message=f"Downloading from {selected['provider']}: {selected['artist']} - {selected['title']}",
        state_data_updates={
            'selected_audio_index': selection_index,
            'selected_audio_provider': selected['provider'],
        }
    )
    
    try:
        # Get source info from selected result
        source_name = selected.get('provider')
        source_id = selected.get('source_id')
        target_file = selected.get('target_file')
        download_url = selected.get('url')

        # Route to appropriate download handler based on source type
        if source_name == 'YouTube':
            # Use YouTubeDownloadService for all YouTube downloads
            # This ensures consistent handling whether remote is configured or not
            youtube_service = get_youtube_download_service()

            if not source_id and download_url:
                # Extract video ID from URL if not in source_id
                source_id = youtube_service._extract_video_id(download_url)

            if not source_id:
                raise DownloadError(f"No video ID available for YouTube download")

            logger.info(f"YouTube download via YouTubeDownloadService: video_id={source_id}")

            try:
                audio_gcs_path = await youtube_service.download_by_id(
                    video_id=source_id,
                    job_id=job_id,
                    artist=selected.get('artist'),
                    title=selected.get('title'),
                )
                filename = os.path.basename(audio_gcs_path)
            except YouTubeDownloadError as e:
                raise DownloadError(f"YouTube download failed: {e}")

        elif source_name in ['RED', 'OPS']:
            # Torrent sources - must use remote flacfetch
            if not audio_search_service.is_remote_enabled():
                raise DownloadError(
                    f"Cannot download from {source_name} without remote flacfetch service. "
                    "Configure FLACFETCH_API_URL."
                )

            gcs_destination = f"uploads/{job_id}/audio/"

            if source_id:
                logger.info(f"Torrent download via download_by_id: {source_name} ID={source_id}")
                result = audio_search_service.download_by_id(
                    source_name=source_name,
                    source_id=source_id,
                    output_dir="",
                    target_file=target_file,
                    download_url=download_url,
                    gcs_path=gcs_destination,
                )
            else:
                logger.info(f"Torrent download via search-based download")
                result = audio_search_service.download(
                    result_index=selection_index,
                    output_dir="",
                    gcs_path=gcs_destination,
                    remote_search_id=remote_search_id,
                )

            # Extract GCS path from result
            audio_gcs_path = _extract_gcs_path(result.filepath)
            filename = os.path.basename(result.filepath)
            logger.info(f"Torrent download complete: {audio_gcs_path}")

        elif source_name == 'Spotify':
            # Spotify downloads - use audio_search_service
            if not audio_search_service.is_remote_enabled():
                raise DownloadError(
                    f"Cannot download from Spotify without remote flacfetch service. "
                    "Configure FLACFETCH_API_URL."
                )

            gcs_destination = f"uploads/{job_id}/audio/"
            logger.info(f"Spotify download: source_id={source_id}")

            result = audio_search_service.download_by_id(
                source_name=source_name,
                source_id=source_id,
                output_dir="",
                download_url=download_url,
                gcs_path=gcs_destination,
            )

            audio_gcs_path = _extract_gcs_path(result.filepath)
            filename = os.path.basename(result.filepath)
            logger.info(f"Spotify download complete: {audio_gcs_path}")

        else:
            # Unknown source - try generic download
            logger.warning(f"Unknown source type: {source_name}, attempting generic download")
            temp_dir = tempfile.mkdtemp(prefix=f"audio_download_{job_id}_")

            try:
                result = audio_search_service.download(
                    result_index=selection_index,
                    output_dir=temp_dir,
                    remote_search_id=remote_search_id,
                )

                filename = os.path.basename(result.filepath)
                audio_gcs_path = f"uploads/{job_id}/audio/{filename}"

                content_type, _ = mimetypes.guess_type(result.filepath)
                with open(result.filepath, 'rb') as f:
                    storage_service.upload_fileobj(
                        f,
                        audio_gcs_path,
                        content_type=content_type or 'application/octet-stream'
                    )

                logger.info(f"Generic download uploaded to GCS: {audio_gcs_path}")
            finally:
                # Clean up temp files
                try:
                    if os.path.exists(temp_dir):
                        import shutil
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to clean up temp files: {e}")
        
        # Update job with GCS path and transition to DOWNLOADING
        job_manager.update_job(job_id, {
            'input_media_gcs_path': audio_gcs_path,
            'filename': filename,
        })
        
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.DOWNLOADING,
            progress=15,
            message="Audio downloaded, starting processing"
        )
        
        # Trigger workers
        background_tasks.add_task(_trigger_workers_parallel, job_id)
        
        return {
            'selected_index': selection_index,
            'selected_title': selected['title'],
            'selected_artist': selected['artist'],
            'selected_provider': selected['provider'],
        }
        
    except DownloadError as e:
        job_manager.fail_job(job_id, f"Audio download failed: {e}")
        raise HTTPException(status_code=500, detail=f"Download failed: {e}")
    except Exception as e:
        job_manager.fail_job(job_id, f"Audio download failed: {e}")
        raise HTTPException(status_code=500, detail=f"Download failed: {e}")


@router.post("/audio-search/search", response_model=AudioSearchResponse)
async def search_audio(
    request: Request,
    background_tasks: BackgroundTasks,
    body: AudioSearchRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Search for audio by artist and title, creating a new job.

    This endpoint:
    1. Creates a job in PENDING state
    2. Searches for audio using flacfetch
    3. Either returns search results for user selection, or
    4. If auto_download=True, automatically selects best and starts processing

    Use cases:
    - Interactive mode (default): Returns results, user calls /select endpoint
    - Auto mode (auto_download=True): Automatically selects and downloads best
    """
    # Check tenant feature flag
    tenant_config = get_tenant_config_from_request(request)
    if tenant_config and not tenant_config.features.audio_search:
        raise HTTPException(
            status_code=403,
            detail="Audio search is not available for this portal"
        )

    try:
        # Apply default distribution settings
        settings = get_settings()
        effective_dropbox_path = body.dropbox_path or settings.default_dropbox_path
        effective_gdrive_folder_id = body.gdrive_folder_id or settings.default_gdrive_folder_id
        effective_discord_webhook_url = body.discord_webhook_url or settings.default_discord_webhook_url

        # Apply defaults for YouTube/Dropbox distribution (for web service)
        # Use explicit value if provided, otherwise fall back to server default
        effective_enable_youtube_upload = body.enable_youtube_upload if body.enable_youtube_upload is not None else settings.default_enable_youtube_upload
        effective_brand_prefix = body.brand_prefix or settings.default_brand_prefix
        effective_youtube_description = body.youtube_description or settings.default_youtube_description

        # Validate credentials if distribution services are requested
        invalid_services = []
        credential_manager = get_credential_manager()

        if effective_enable_youtube_upload:
            result = credential_manager.check_youtube_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"youtube ({result.message})")
        
        if effective_dropbox_path:
            result = credential_manager.check_dropbox_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"dropbox ({result.message})")
        
        if effective_gdrive_folder_id:
            result = credential_manager.check_gdrive_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"gdrive ({result.message})")
        
        if invalid_services:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "credentials_invalid",
                    "message": f"Distribution services need re-authorization: {', '.join(invalid_services)}",
                    "invalid_services": invalid_services,
                }
            )
        
        # Extract request metadata
        request_metadata = extract_request_metadata(request, created_from="audio_search")

        # Apply default theme if none specified
        # This ensures all karaoke videos use the Nomad theme by default
        effective_theme_id = body.theme_id
        if effective_theme_id is None:
            theme_service = get_theme_service()
            effective_theme_id = theme_service.get_default_theme_id()
            if effective_theme_id:
                logger.info(f"Applying default theme: {effective_theme_id}")

        # Resolve CDG/TXT defaults based on theme
        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(
            effective_theme_id, body.enable_cdg, body.enable_txt
        )

        # Use authenticated user's email
        effective_user_email = auth_result.user_email

        # Determine display values - use display_* if provided, otherwise fall back to search values
        # Display values are used for title screens, filenames, YouTube, etc.
        # Search values (body.artist, body.title) are used for audio search
        effective_display_artist = body.display_artist.strip() if body.display_artist else body.artist
        effective_display_title = body.display_title.strip() if body.display_title else body.title

        # Create job
        job_create = JobCreate(
            artist=effective_display_artist,  # Display value for title screens, filenames
            title=effective_display_title,    # Display value for title screens, filenames
            theme_id=effective_theme_id,
            color_overrides=body.color_overrides or {},
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            brand_prefix=effective_brand_prefix,
            enable_youtube_upload=effective_enable_youtube_upload,
            youtube_description=effective_youtube_description,
            youtube_description_template=effective_youtube_description,  # video_worker reads this field
            discord_webhook_url=effective_discord_webhook_url,
            dropbox_path=effective_dropbox_path,
            gdrive_folder_id=effective_gdrive_folder_id,
            user_email=effective_user_email,
            lyrics_artist=body.lyrics_artist,
            lyrics_title=body.lyrics_title,
            subtitle_offset_ms=body.subtitle_offset_ms,
            clean_instrumental_model=body.clean_instrumental_model,
            backing_vocals_models=body.backing_vocals_models,
            other_stems_models=body.other_stems_models,
            audio_search_artist=body.artist,
            audio_search_title=body.title,
            auto_download=body.auto_download,
            request_metadata=request_metadata,
            non_interactive=body.non_interactive,
            # Tenant scoping
            tenant_id=tenant_config.id if tenant_config else None,
        )
        job = job_manager.create_job(job_create, is_admin=auth_result.is_admin)
        job_id = job.job_id

        logger.info(f"Created job {job_id} for audio search: {body.artist} - {body.title}")
        
        # Update job with audio search fields
        job_manager.update_job(job_id, {
            'audio_search_artist': body.artist,
            'audio_search_title': body.title,
            'auto_download': body.auto_download,
        })

        # If theme is set and no custom style files are being uploaded, prepare theme style now
        # This copies the theme's style_params.json to the job folder so LyricsTranscriber
        # can access the style configuration for preview videos
        if effective_theme_id and not body.style_files:
            from backend.api.routes.file_upload import _prepare_theme_for_job
            try:
                style_params_path, theme_style_assets, youtube_desc = _prepare_theme_for_job(
                    job_id, effective_theme_id, body.color_overrides
                )
                theme_update = {
                    'style_params_gcs_path': style_params_path,
                    'style_assets': theme_style_assets,
                }
                if youtube_desc and not effective_youtube_description:
                    theme_update['youtube_description_template'] = youtube_desc
                job_manager.update_job(job_id, theme_update)
                logger.info(f"Applied theme '{effective_theme_id}' to job {job_id}")
            except Exception as e:
                logger.warning(f"Failed to prepare theme '{effective_theme_id}' for job {job_id}: {e}")
                # Continue without theme - job can still be processed with defaults

        # Handle style file uploads if provided
        style_upload_urls: List[StyleUploadUrl] = []
        style_assets = {}
        
        if body.style_files:
            logger.info(f"Processing {len(body.style_files)} style file uploads for job {job_id}")
            
            for file_info in body.style_files:
                # Validate file type
                if file_info.file_type not in STYLE_FILE_TYPES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid file type '{file_info.file_type}'. Must be one of: {', '.join(STYLE_FILE_TYPES.keys())}"
                    )
                
                # Validate extension
                ext = Path(file_info.filename).suffix.lower()
                allowed_exts = STYLE_FILE_TYPES[file_info.file_type]
                if ext not in allowed_exts:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid extension '{ext}' for {file_info.file_type}. Allowed: {', '.join(allowed_exts)}"
                    )
                
                # Generate GCS path
                if file_info.file_type == 'style_params':
                    gcs_path = f"uploads/{job_id}/style/style_params.json"
                else:
                    # style_intro_background -> intro_background, etc.
                    asset_key = file_info.file_type.replace('style_', '')
                    gcs_path = f"uploads/{job_id}/style/{asset_key}{ext}"
                
                # Generate signed upload URL
                signed_url = storage_service.generate_signed_upload_url(
                    gcs_path,
                    content_type=file_info.content_type,
                    expiration_minutes=60
                )
                
                style_upload_urls.append(StyleUploadUrl(
                    file_type=file_info.file_type,
                    gcs_path=gcs_path,
                    upload_url=signed_url
                ))
                
                # Track the expected asset paths
                if file_info.file_type == 'style_params':
                    style_assets['style_params'] = gcs_path
                else:
                    asset_key = file_info.file_type.replace('style_', '')
                    style_assets[asset_key] = gcs_path
            
            # Update job with style asset expectations
            style_update = {'style_assets': style_assets}
            if 'style_params' in style_assets:
                style_update['style_params_gcs_path'] = style_assets['style_params']
            job_manager.update_job(job_id, style_update)
            
            logger.info(f"Generated {len(style_upload_urls)} style upload URLs for job {job_id}")
        
        # Transition to searching state
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.SEARCHING_AUDIO,
            progress=5,
            message=f"Searching for: {body.artist} - {body.title}"
        )
        
        # Perform search
        audio_search_service = get_audio_search_service()
        
        try:
            search_results = audio_search_service.search(body.artist, body.title)
        except NoResultsError as e:
            job_manager.fail_job(job_id, f"No audio sources found for: {body.artist} - {body.title}")
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "no_results",
                    "message": str(e),
                    "job_id": job_id,
                }
            )
        except AudioSearchError as e:
            job_manager.fail_job(job_id, f"Audio search failed: {e}")
            raise HTTPException(status_code=500, detail=f"Search failed: {e}")
        
        # Store results in job state_data, including remote_search_id if available
        results_dicts = [r.to_dict() for r in search_results]
        state_data_update = {
            'audio_search_results': results_dicts,
            'audio_search_count': len(results_dicts),
        }
        # Store remote_search_id for use during download (important for concurrent requests)
        if audio_search_service.last_remote_search_id:
            state_data_update['remote_search_id'] = audio_search_service.last_remote_search_id
        job_manager.update_job(job_id, {
            'state_data': state_data_update
        })
        
        # If auto_download, select best and start processing
        if body.auto_download:
            best_index = audio_search_service.select_best(search_results)
            
            logger.info(f"Auto-download enabled, selecting result {best_index}")
            
            selection_info = await _download_and_start_processing(
                job_id=job_id,
                selection_index=best_index,
                audio_search_service=audio_search_service,
                background_tasks=background_tasks,
            )
            
            return AudioSearchResponse(
                status="success",
                job_id=job_id,
                message=f"Audio found and download started: {selection_info['selected_artist']} - {selection_info['selected_title']} ({selection_info['selected_provider']})",
                results=None,  # Don't return results in auto mode
                results_count=len(search_results),
                auto_download=True,
                server_version=VERSION,
                style_upload_urls=style_upload_urls if style_upload_urls else None,
            )
        
        # Interactive mode: return results for user selection
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.AWAITING_AUDIO_SELECTION,
            progress=10,
            message=f"Found {len(search_results)} audio sources. Waiting for selection."
        )
        
        # Convert to response format with full Release data for rich display
        result_responses = []
        for r in search_results:
            # Get full serialized data from raw_result if available
            # raw_result can be either:
            # - A dict (from remote flacfetch API)
            # - A Release object (from local flacfetch)
            raw_dict = {}
            if r.raw_result:
                if isinstance(r.raw_result, dict):
                    # Remote flacfetch API returns dicts directly
                    raw_dict = r.raw_result
                else:
                    # Local flacfetch returns Release objects
                    try:
                        raw_dict = r.raw_result.to_dict()
                    except AttributeError:
                        pass  # Not a Release object
            
            result_responses.append(
                AudioSearchResultResponse(
                    index=r.index,
                    title=r.title,
                    artist=r.artist,
                    provider=r.provider,
                    url=r.url,
                    duration=r.duration,
                    quality=r.quality,
                    source_id=r.source_id,
                    seeders=raw_dict.get('seeders') or r.seeders,
                    target_file=raw_dict.get('target_file') or r.target_file,
                    # Additional fields for rich display
                    year=raw_dict.get('year'),
                    label=raw_dict.get('label'),
                    edition_info=raw_dict.get('edition_info'),
                    release_type=raw_dict.get('release_type'),
                    channel=raw_dict.get('channel'),
                    view_count=raw_dict.get('view_count'),
                    size_bytes=raw_dict.get('size_bytes'),
                    target_file_size=raw_dict.get('target_file_size'),
                    track_pattern=raw_dict.get('track_pattern'),
                    match_score=raw_dict.get('match_score'),
                    # Pre-computed display fields
                    formatted_size=raw_dict.get('formatted_size'),
                    formatted_duration=raw_dict.get('formatted_duration'),
                    formatted_views=raw_dict.get('formatted_views'),
                    is_lossless=raw_dict.get('is_lossless'),
                    quality_str=raw_dict.get('quality_str'),
                    # Full quality object for Release.from_dict()
                    # Remote API uses 'quality_data', local uses 'quality' for the dict
                    quality_data=raw_dict.get('quality_data') or raw_dict.get('quality'),
                )
            )
        
        return AudioSearchResponse(
            status="awaiting_selection",
            job_id=job_id,
            message=f"Found {len(search_results)} audio sources. Call /api/audio-search/{job_id}/select to choose one.",
            results=result_responses,
            results_count=len(search_results),
            auto_download=False,
            server_version=VERSION,
            style_upload_urls=style_upload_urls if style_upload_urls else None,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in audio search: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audio-search/{job_id}/results")
async def get_audio_search_results(
    job_id: str,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Get audio search results for a job.
    
    Returns the cached search results so user can select one.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    search_results = job.state_data.get('audio_search_results', [])
    
    if not search_results:
        raise HTTPException(
            status_code=400,
            detail="No search results available for this job"
        )
    
    return {
        "status": "success",
        "job_id": job_id,
        "job_status": job.status,
        "artist": job.audio_search_artist or job.artist,
        "title": job.audio_search_title or job.title,
        "results": search_results,
        "results_count": len(search_results),
    }


@router.post("/audio-search/{job_id}/select", response_model=AudioSelectResponse)
async def select_audio_source(
    job_id: str,
    background_tasks: BackgroundTasks,
    body: AudioSelectRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Select an audio source and start job processing.
    
    This endpoint:
    1. Validates the job is awaiting selection
    2. Downloads the selected audio
    3. Uploads to GCS
    4. Triggers audio and lyrics workers
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    # Verify job is awaiting selection
    if job.status != JobStatus.AWAITING_AUDIO_SELECTION:
        raise HTTPException(
            status_code=400,
            detail=f"Job is not awaiting audio selection (status: {job.status})"
        )
    
    # Get search service instance
    # Note: With download_by_id, we no longer need to re-search to populate the cache.
    # The source_id stored in job.state_data['audio_search_results'] is sufficient.
    audio_search_service = get_audio_search_service()

    # Validate search results exist in job state_data
    search_results = job.state_data.get('audio_search_results', [])
    if not search_results:
        raise HTTPException(status_code=400, detail="No search results cached for this job")
    
    selection_info = await _download_and_start_processing(
        job_id=job_id,
        selection_index=body.selection_index,
        audio_search_service=audio_search_service,
        background_tasks=background_tasks,
    )
    
    return AudioSelectResponse(
        status="success",
        job_id=job_id,
        message="Audio selected and download started",
        selected_index=selection_info['selected_index'],
        selected_title=selection_info['selected_title'],
        selected_artist=selection_info['selected_artist'],
        selected_provider=selection_info['selected_provider'],
    )


