"""
File upload route for local file submission with style configuration support.

Supports two upload flows:
1. Direct upload (original): All files sent as multipart form data to /api/jobs/upload
   - Simple but limited by Cloud Run's 32MB request body size

2. Signed URL upload (recommended for large files):
   - POST /api/jobs/create-with-upload-urls - Creates job, returns signed GCS upload URLs
   - Client uploads files directly to GCS using signed URLs (no size limit)
   - POST /api/jobs/{job_id}/uploads-complete - Validates uploads, triggers workers
"""
import asyncio
import json
import logging
import tempfile
import os
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Request, Body, Depends
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from pydantic import BaseModel, Field

from backend.models.job import JobCreate, JobStatus
from backend.models.theme import ColorOverrides
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.worker_service import get_worker_service
from backend.services.credential_manager import get_credential_manager, CredentialStatus
from backend.services.theme_service import get_theme_service
from backend.services.job_defaults_service import (
    get_effective_distribution_settings,
    resolve_cdg_txt_defaults,
    EffectiveDistributionSettings,
)
from backend.config import get_settings
from backend.version import VERSION
from backend.services.metrics import metrics
from backend.api.dependencies import require_auth
from backend.services.auth_service import UserType, AuthResult
from backend.middleware.tenant import get_tenant_config_from_request
from backend.services.youtube_download_service import (
    get_youtube_download_service,
    YouTubeDownloadError,
)

logger = logging.getLogger(__name__)


def _is_youtube_url(url: str) -> bool:
    """Check if a URL is a YouTube URL."""
    return any(domain in url.lower() for domain in [
        'youtube.com', 'youtu.be', 'youtube-nocookie.com'
    ])
router = APIRouter(tags=["jobs"])


# ============================================================================
# Pydantic models for signed URL upload flow
# ============================================================================

class FileUploadRequest(BaseModel):
    """Information about a file to be uploaded."""
    filename: str = Field(..., description="Original filename with extension")
    content_type: str = Field(..., description="MIME type of the file")
    file_type: str = Field(..., description="Type of file: 'audio', 'style_params', 'style_intro_background', 'style_karaoke_background', 'style_end_background', 'style_font', 'style_cdg_instrumental_background', 'style_cdg_title_background', 'style_cdg_outro_background', 'lyrics_file'")


class CreateJobFromUrlRequest(BaseModel):
    """Request to create a job from a YouTube/online URL."""
    # Required fields
    url: str = Field(..., description="YouTube or other video URL to download audio from")

    # Optional fields - will be auto-detected from URL if not provided
    artist: Optional[str] = Field(None, description="Artist name (auto-detected from URL if not provided)")
    title: Optional[str] = Field(None, description="Song title (auto-detected from URL if not provided)")

    # Theme configuration (use pre-made theme from GCS)
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
    webhook_url: Optional[str] = Field(None, description="Generic webhook URL")
    user_email: Optional[str] = Field(None, description="User email for notifications")

    # Distribution options (native API - preferred for remote CLI)
    dropbox_path: Optional[str] = Field(None, description="Dropbox folder path for organized output")
    gdrive_folder_id: Optional[str] = Field(None, description="Google Drive folder ID for public share uploads")

    # Legacy distribution options (rclone - deprecated)
    organised_dir_rclone_root: Optional[str] = Field(None, description="[Deprecated] rclone remote path")

    # Lyrics configuration
    lyrics_artist: Optional[str] = Field(None, description="Override artist name for lyrics search")
    lyrics_title: Optional[str] = Field(None, description="Override title for lyrics search")
    subtitle_offset_ms: int = Field(0, description="Subtitle timing offset in milliseconds")

    # Audio separation model configuration
    clean_instrumental_model: Optional[str] = Field(None, description="Model for clean instrumental separation")
    backing_vocals_models: Optional[List[str]] = Field(None, description="Models for backing vocals separation")
    other_stems_models: Optional[List[str]] = Field(None, description="Models for other stems")

    # Non-interactive mode
    non_interactive: bool = Field(False, description="Skip interactive steps (lyrics review, instrumental selection)")


class CreateJobFromUrlResponse(BaseModel):
    """Response from creating a job from URL."""
    status: str
    job_id: str
    message: str
    detected_artist: Optional[str]
    detected_title: Optional[str]
    server_version: str


class CreateJobWithUploadUrlsRequest(BaseModel):
    """Request to create a job and get signed upload URLs."""
    # Required fields
    artist: str = Field(..., description="Artist name")
    title: str = Field(..., description="Song title")
    files: List[FileUploadRequest] = Field(..., description="List of files to upload")

    # Theme configuration (use pre-made theme from GCS)
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
    webhook_url: Optional[str] = Field(None, description="Generic webhook URL")
    user_email: Optional[str] = Field(None, description="User email for notifications")
    
    # Distribution options (native API - preferred for remote CLI)
    dropbox_path: Optional[str] = Field(None, description="Dropbox folder path for organized output")
    gdrive_folder_id: Optional[str] = Field(None, description="Google Drive folder ID for public share uploads")
    
    # Legacy distribution options (rclone - deprecated)
    organised_dir_rclone_root: Optional[str] = Field(None, description="[Deprecated] rclone remote path")
    
    # Lyrics configuration
    lyrics_artist: Optional[str] = Field(None, description="Override artist name for lyrics search")
    lyrics_title: Optional[str] = Field(None, description="Override title for lyrics search")
    subtitle_offset_ms: int = Field(0, description="Subtitle timing offset in milliseconds")
    
    # Audio separation model configuration
    clean_instrumental_model: Optional[str] = Field(None, description="Model for clean instrumental separation")
    backing_vocals_models: Optional[List[str]] = Field(None, description="Models for backing vocals separation")
    other_stems_models: Optional[List[str]] = Field(None, description="Models for other stems")
    
    # Existing instrumental configuration (Batch 3)
    existing_instrumental: bool = Field(False, description="Whether an existing instrumental file is being uploaded")
    
    # Two-phase workflow configuration (Batch 6)
    prep_only: bool = Field(False, description="Stop after review phase, don't run finalisation")
    keep_brand_code: Optional[str] = Field(None, description="Preserve existing brand code instead of generating new one")

    # Non-interactive mode
    non_interactive: bool = Field(False, description="Skip interactive steps (lyrics review, instrumental selection)")


class SignedUploadUrl(BaseModel):
    """Signed URL for uploading a file."""
    file_type: str = Field(..., description="Type of file this URL is for")
    gcs_path: str = Field(..., description="The GCS path where file will be stored")
    upload_url: str = Field(..., description="Signed URL to PUT the file to")
    content_type: str = Field(..., description="Content-Type header to use when uploading")


class CreateJobWithUploadUrlsResponse(BaseModel):
    """Response from creating a job with upload URLs."""
    status: str
    job_id: str
    message: str
    upload_urls: List[SignedUploadUrl]
    server_version: str


class UploadsCompleteRequest(BaseModel):
    """Request to mark uploads as complete and start processing."""
    uploaded_files: List[str] = Field(..., description="List of file_types that were successfully uploaded")

# Initialize services
job_manager = JobManager()
storage_service = StorageService()
worker_service = get_worker_service()


def extract_request_metadata(request: Request, created_from: str = "upload") -> Dict[str, Any]:
    """
    Extract metadata from a FastAPI Request for job tracking.
    
    Captures:
    - Client IP address (handles X-Forwarded-For for proxies)
    - User-Agent header
    - Environment from X-Environment header (test/production/development)
    - Client ID from X-Client-ID header
    - All custom X-* headers
    - Server version
    - Creation source (upload/url)
    
    Args:
        request: FastAPI Request object
        created_from: How the job was created ("upload" or "url")
        
    Returns:
        Dict with metadata fields for storage in job.request_metadata
    """
    headers = dict(request.headers)
    
    # Extract client IP (check X-Forwarded-For for proxy scenarios)
    client_ip = headers.get('x-forwarded-for', '').split(',')[0].strip()
    if not client_ip and request.client:
        client_ip = request.client.host
    
    # Extract standard headers
    user_agent = headers.get('user-agent', '')
    environment = headers.get('x-environment', '')  # test, production, development
    client_id = headers.get('x-client-id', '')  # Customer/user identifier
    
    # Collect all X-* custom headers (excluding standard ones we already captured)
    custom_headers = {}
    for key, value in headers.items():
        if key.lower().startswith('x-') and key.lower() not in ('x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host'):
            # Normalize header name to original casing if possible
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


# File extension validation
ALLOWED_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.aac'}
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
ALLOWED_FONT_EXTENSIONS = {'.ttf', '.otf', '.woff', '.woff2'}


async def _trigger_workers_parallel(job_id: str) -> None:
    """
    Trigger both audio and lyrics workers in parallel.

    FastAPI's BackgroundTasks runs async tasks sequentially, so we use
    asyncio.gather to ensure both workers start at the same time.
    """
    await asyncio.gather(
        worker_service.trigger_audio_worker(job_id),
        worker_service.trigger_lyrics_worker(job_id)
    )


async def _trigger_audio_worker_only(job_id: str) -> None:
    """
    Trigger only the audio worker.

    Used for URL jobs where audio needs to be downloaded first.
    The audio worker will trigger the lyrics worker after download completes.
    """
    await worker_service.trigger_audio_worker(job_id)


def _prepare_theme_for_job(
    job_id: str,
    theme_id: str,
    color_overrides: Optional[Dict[str, str]] = None
) -> Tuple[str, Dict[str, str], Optional[str]]:
    """
    Prepare theme style files for a job.

    Args:
        job_id: The job ID
        theme_id: Theme identifier
        color_overrides: Optional color override dict

    Returns:
        Tuple of (style_params_gcs_path, style_assets, youtube_description)

    Raises:
        HTTPException: If theme not found
    """
    theme_service = get_theme_service()

    # Verify theme exists
    if not theme_service.theme_exists(theme_id):
        raise HTTPException(
            status_code=400,
            detail=f"Theme not found: {theme_id}. Use GET /api/themes to list available themes."
        )

    # Convert dict to ColorOverrides model if provided
    color_overrides_model = None
    if color_overrides:
        color_overrides_model = ColorOverrides(**color_overrides)

    # Prepare job style from theme
    style_params_path, style_assets = theme_service.prepare_job_style(
        job_id=job_id,
        theme_id=theme_id,
        color_overrides=color_overrides_model
    )

    # Get YouTube description template if available
    youtube_desc = theme_service.get_youtube_description(theme_id)

    logger.info(f"Prepared theme '{theme_id}' for job {job_id}")

    return style_params_path, style_assets, youtube_desc


@router.post("/jobs/upload")
async def upload_and_create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    auth_result: AuthResult = Depends(require_auth),
    # Required fields
    file: UploadFile = File(..., description="Audio file to process"),
    artist: str = Form(..., description="Artist name"),
    title: str = Form(..., description="Song title"),
    # Style configuration files (optional)
    style_params: Optional[UploadFile] = File(None, description="Style parameters JSON file"),
    style_intro_background: Optional[UploadFile] = File(None, description="Intro/title screen background image"),
    style_karaoke_background: Optional[UploadFile] = File(None, description="Karaoke video background image"),
    style_end_background: Optional[UploadFile] = File(None, description="End screen background image"),
    style_font: Optional[UploadFile] = File(None, description="Font file (TTF/OTF)"),
    style_cdg_instrumental_background: Optional[UploadFile] = File(None, description="CDG instrumental background"),
    style_cdg_title_background: Optional[UploadFile] = File(None, description="CDG title screen background"),
    style_cdg_outro_background: Optional[UploadFile] = File(None, description="CDG outro screen background"),
    # Theme configuration (use pre-made theme from GCS)
    theme_id: Optional[str] = Form(None, description="Theme ID to use (e.g., 'nomad', 'default'). If set, CDG/TXT are enabled by default."),
    color_overrides: Optional[str] = Form(None, description="JSON-encoded color overrides: artist_color, title_color, sung_lyrics_color, unsung_lyrics_color (hex #RRGGBB)"),
    # Processing options (CDG/TXT require style config or theme, disabled by default unless theme is set)
    enable_cdg: Optional[bool] = Form(None, description="Generate CDG+MP3 package. Default: True if theme_id set, False otherwise"),
    enable_txt: Optional[bool] = Form(None, description="Generate TXT+MP3 package. Default: True if theme_id set, False otherwise"),
    # Finalisation options
    brand_prefix: Optional[str] = Form(None, description="Brand code prefix (e.g., NOMAD)"),
    enable_youtube_upload: Optional[bool] = Form(None, description="Upload to YouTube. None = use server default"),
    youtube_description: Optional[str] = Form(None, description="YouTube video description text"),
    discord_webhook_url: Optional[str] = Form(None, description="Discord webhook URL for notifications"),
    webhook_url: Optional[str] = Form(None, description="Generic webhook URL"),
    user_email: Optional[str] = Form(None, description="User email for notifications"),
    # Distribution options (native API - preferred for remote CLI)
    dropbox_path: Optional[str] = Form(None, description="Dropbox folder path for organized output (e.g., /Karaoke/Tracks-Organized)"),
    gdrive_folder_id: Optional[str] = Form(None, description="Google Drive folder ID for public share uploads"),
    # Legacy distribution options (rclone - deprecated)
    organised_dir_rclone_root: Optional[str] = Form(None, description="[Deprecated] rclone remote path for Dropbox upload"),
    # Lyrics configuration (overrides for search/transcription)
    lyrics_artist: Optional[str] = Form(None, description="Override artist name for lyrics search"),
    lyrics_title: Optional[str] = Form(None, description="Override title for lyrics search"),
    lyrics_file: Optional[UploadFile] = File(None, description="User-provided lyrics file (TXT, DOCX, RTF)"),
    subtitle_offset_ms: int = Form(0, description="Subtitle timing offset in milliseconds (positive = delay)"),
    # Audio separation model configuration
    clean_instrumental_model: Optional[str] = Form(None, description="Model for clean instrumental separation (e.g., model_bs_roformer_ep_317_sdr_12.9755.ckpt)"),
    backing_vocals_models: Optional[str] = Form(None, description="Comma-separated list of models for backing vocals separation"),
    other_stems_models: Optional[str] = Form(None, description="Comma-separated list of models for other stems (bass, drums, guitar, etc.)"),
    # Non-interactive mode
    non_interactive: bool = Form(False, description="Skip interactive steps (lyrics review, instrumental selection)"),
):
    """
    Upload an audio file and create a karaoke generation job with full style configuration.
    
    This endpoint:
    1. Validates all uploaded files
    2. Creates a job in Firestore
    3. Uploads all files to GCS (audio, style JSON, images, fonts)
    4. Updates job with GCS paths
    5. Triggers the audio and lyrics workers
    
    Style Configuration:
    - style_params: JSON file with style configuration (fonts, colors, regions)
    - style_*_background: Background images for various screens
    - style_font: Custom font file
    
    The style_params JSON can reference the uploaded images/fonts by their original
    filenames, and the backend will update the paths to GCS locations.
    """
    # Check tenant feature flag
    tenant_config = get_tenant_config_from_request(request)
    if tenant_config and not tenant_config.features.file_upload:
        raise HTTPException(
            status_code=403,
            detail="File upload is not available for this portal"
        )

    try:
        # Validate main audio file type
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_AUDIO_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid audio file type '{file_ext}'. Allowed: {', '.join(ALLOWED_AUDIO_EXTENSIONS)}"
            )
        
        # Validate style files if provided
        if style_params and not style_params.filename.endswith('.json'):
            raise HTTPException(status_code=400, detail="Style params must be a JSON file")
        
        for img_file, name in [
            (style_intro_background, "intro_background"),
            (style_karaoke_background, "karaoke_background"),
            (style_end_background, "end_background"),
            (style_cdg_instrumental_background, "cdg_instrumental_background"),
            (style_cdg_title_background, "cdg_title_background"),
            (style_cdg_outro_background, "cdg_outro_background"),
        ]:
            if img_file:
                ext = Path(img_file.filename).suffix.lower()
                if ext not in ALLOWED_IMAGE_EXTENSIONS:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid image file type '{ext}' for {name}. Allowed: {', '.join(ALLOWED_IMAGE_EXTENSIONS)}"
                    )
        
        if style_font:
            ext = Path(style_font.filename).suffix.lower()
            if ext not in ALLOWED_FONT_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid font file type '{ext}'. Allowed: {', '.join(ALLOWED_FONT_EXTENSIONS)}"
                )
        
        # Validate lyrics file if provided
        ALLOWED_LYRICS_EXTENSIONS = {'.txt', '.docx', '.rtf'}
        if lyrics_file:
            ext = Path(lyrics_file.filename).suffix.lower()
            if ext not in ALLOWED_LYRICS_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid lyrics file type '{ext}'. Allowed: {', '.join(ALLOWED_LYRICS_EXTENSIONS)}"
                )
        
        # Apply default distribution settings from environment if not provided
        dist = get_effective_distribution_settings(
            dropbox_path=dropbox_path,
            gdrive_folder_id=gdrive_folder_id,
            discord_webhook_url=discord_webhook_url,
            brand_prefix=brand_prefix,
        )

        if dist.dropbox_path and not dropbox_path:
            logger.info(f"Using default dropbox_path: {dist.dropbox_path}")
        if dist.gdrive_folder_id and not gdrive_folder_id:
            logger.info(f"Using default gdrive_folder_id: {dist.gdrive_folder_id}")
        if dist.discord_webhook_url and not discord_webhook_url:
            logger.info("Using default discord_webhook_url (from env)")
        if dist.brand_prefix and not brand_prefix:
            logger.info(f"Using default brand_prefix: {dist.brand_prefix}")

        # Apply YouTube upload default from settings
        # Use explicit value if provided, otherwise fall back to server default
        settings = get_settings()
        effective_enable_youtube_upload = enable_youtube_upload if enable_youtube_upload is not None else settings.default_enable_youtube_upload
        if effective_enable_youtube_upload and enable_youtube_upload is None:
            logger.info("Using default enable_youtube_upload: True (from env)")

        # Validate credentials for requested distribution services (including defaults)
        # This prevents accepting jobs that will fail later due to missing credentials
        invalid_services = []
        credential_manager = get_credential_manager()

        if effective_enable_youtube_upload:
            result = credential_manager.check_youtube_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"youtube ({result.message})")

        if dist.dropbox_path:
            result = credential_manager.check_dropbox_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"dropbox ({result.message})")

        if dist.gdrive_folder_id:
            result = credential_manager.check_gdrive_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"gdrive ({result.message})")

        if invalid_services:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "credentials_invalid",
                    "message": f"The following distribution services need re-authorization: {', '.join(invalid_services)}",
                    "invalid_services": invalid_services,
                    "auth_url": "/api/auth/status"
                }
            )
        
        # Extract request metadata for tracking and filtering
        request_metadata = extract_request_metadata(request, created_from="upload")

        # Parse color_overrides from JSON if provided
        parsed_color_overrides: Dict[str, str] = {}
        if color_overrides:
            try:
                parsed_color_overrides = json.loads(color_overrides)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid color_overrides JSON: {e}"
                )

        # Apply default theme if none specified
        # This ensures all karaoke videos use the Nomad theme by default
        effective_theme_id = theme_id
        if effective_theme_id is None:
            theme_service = get_theme_service()
            effective_theme_id = theme_service.get_default_theme_id()
            if effective_theme_id:
                logger.info(f"Applying default theme: {effective_theme_id}")

        # Resolve CDG/TXT defaults based on theme
        resolved_cdg, resolved_txt = resolve_cdg_txt_defaults(
            effective_theme_id, enable_cdg, enable_txt
        )

        # Check if any custom style files are being uploaded (overrides theme)
        has_custom_style_files = any([
            style_params,
            style_intro_background,
            style_karaoke_background,
            style_end_background,
            style_font,
            style_cdg_instrumental_background,
            style_cdg_title_background,
            style_cdg_outro_background,
        ])

        # Parse comma-separated model lists into arrays
        parsed_backing_vocals_models = None
        if backing_vocals_models:
            parsed_backing_vocals_models = [m.strip() for m in backing_vocals_models.split(',') if m.strip()]
        
        parsed_other_stems_models = None
        if other_stems_models:
            parsed_other_stems_models = [m.strip() for m in other_stems_models.split(',') if m.strip()]
        
        # Prefer authenticated user's email over form parameter
        effective_user_email = auth_result.user_email or user_email

        # Create job first to get job_id
        job_create = JobCreate(
            artist=artist,
            title=title,
            filename=file.filename,
            theme_id=effective_theme_id,
            color_overrides=parsed_color_overrides,
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            brand_prefix=dist.brand_prefix,
            enable_youtube_upload=effective_enable_youtube_upload,
            youtube_description=youtube_description,
            discord_webhook_url=dist.discord_webhook_url,
            webhook_url=webhook_url,
            user_email=effective_user_email,
            # Native API distribution (preferred for remote CLI)
            dropbox_path=dist.dropbox_path,
            gdrive_folder_id=dist.gdrive_folder_id,
            # Legacy rclone distribution (deprecated)
            organised_dir_rclone_root=organised_dir_rclone_root,
            # Lyrics configuration (overrides for search/transcription)
            lyrics_artist=lyrics_artist,
            lyrics_title=lyrics_title,
            subtitle_offset_ms=subtitle_offset_ms,
            # Audio separation model configuration
            clean_instrumental_model=clean_instrumental_model,
            backing_vocals_models=parsed_backing_vocals_models,
            other_stems_models=parsed_other_stems_models,
            # Request metadata for tracking and filtering
            request_metadata=request_metadata,
            # Non-interactive mode
            non_interactive=non_interactive,
            # Tenant scoping
            tenant_id=tenant_config.id if tenant_config else None,
        )
        job = job_manager.create_job(job_create, is_admin=auth_result.is_admin)
        job_id = job.job_id

        # Record job creation metric
        metrics.record_job_created(job_id, source="upload")

        logger.info(f"Created job {job_id} for {artist} - {title}")

        # If theme is set and no custom style files are being uploaded, prepare theme style now
        # This copies the theme's style_params.json to the job folder so LyricsTranscriber
        # can access the style configuration for preview videos
        theme_style_params_path = None
        theme_style_assets = {}
        theme_youtube_desc = None
        if effective_theme_id and not has_custom_style_files:
            try:
                theme_style_params_path, theme_style_assets, theme_youtube_desc = _prepare_theme_for_job(
                    job_id, effective_theme_id, parsed_color_overrides or None
                )
                logger.info(f"Applied theme '{effective_theme_id}' to job {job_id}")
            except HTTPException:
                raise  # Re-raise validation errors (e.g., theme not found)
            except Exception as e:
                logger.warning(f"Failed to prepare theme '{effective_theme_id}' for job {job_id}: {e}")
                # Continue without theme - job can still be processed with defaults

        # Upload main audio file to GCS
        audio_gcs_path = f"uploads/{job_id}/audio/{file.filename}"
        logger.info(f"Uploading audio to GCS: {audio_gcs_path}")
        storage_service.upload_fileobj(
            file.file,
            audio_gcs_path,
            content_type=file.content_type or 'audio/flac'
        )
        
        # Track style assets
        style_assets = {}
        
        # Upload style files if provided
        if style_params:
            style_gcs_path = f"uploads/{job_id}/style/style_params.json"
            logger.info(f"Uploading style params to GCS: {style_gcs_path}")
            storage_service.upload_fileobj(
                style_params.file,
                style_gcs_path,
                content_type='application/json'
            )
            style_assets['style_params'] = style_gcs_path
        
        # Upload background images
        for img_file, asset_key in [
            (style_intro_background, "intro_background"),
            (style_karaoke_background, "karaoke_background"),
            (style_end_background, "end_background"),
            (style_cdg_instrumental_background, "cdg_instrumental_background"),
            (style_cdg_title_background, "cdg_title_background"),
            (style_cdg_outro_background, "cdg_outro_background"),
        ]:
            if img_file:
                gcs_path = f"uploads/{job_id}/style/{asset_key}{Path(img_file.filename).suffix.lower()}"
                logger.info(f"Uploading {asset_key} to GCS: {gcs_path}")
                storage_service.upload_fileobj(
                    img_file.file,
                    gcs_path,
                    content_type=img_file.content_type or 'image/png'
                )
                style_assets[asset_key] = gcs_path
        
        # Upload font file
        if style_font:
            font_gcs_path = f"uploads/{job_id}/style/font{Path(style_font.filename).suffix.lower()}"
            logger.info(f"Uploading font to GCS: {font_gcs_path}")
            storage_service.upload_fileobj(
                style_font.file,
                font_gcs_path,
                content_type='font/ttf'
            )
            style_assets['font'] = font_gcs_path
        
        # Upload lyrics file if provided
        lyrics_file_gcs_path = None
        if lyrics_file:
            lyrics_file_gcs_path = f"uploads/{job_id}/lyrics/user_lyrics{Path(lyrics_file.filename).suffix.lower()}"
            logger.info(f"Uploading user lyrics file to GCS: {lyrics_file_gcs_path}")
            storage_service.upload_fileobj(
                lyrics_file.file,
                lyrics_file_gcs_path,
                content_type='text/plain'
            )
        
        # Update job with all GCS paths
        update_data = {
            'input_media_gcs_path': audio_gcs_path,
            'filename': file.filename,
            'enable_cdg': resolved_cdg,
            'enable_txt': resolved_txt,
        }

        # Handle style assets - either from custom uploads or from theme
        if style_assets:
            # Custom style files uploaded
            update_data['style_assets'] = style_assets
            if 'style_params' in style_assets:
                update_data['style_params_gcs_path'] = style_assets['style_params']
        elif theme_style_assets:
            # Theme style assets (no custom uploads)
            update_data['style_assets'] = theme_style_assets
            if theme_style_params_path:
                update_data['style_params_gcs_path'] = theme_style_params_path

        if dist.brand_prefix:
            update_data['brand_prefix'] = dist.brand_prefix
        if dist.discord_webhook_url:
            update_data['discord_webhook_url'] = dist.discord_webhook_url
        # Use theme YouTube description if no custom one provided
        effective_youtube_description = youtube_description or theme_youtube_desc
        if effective_youtube_description:
            update_data['youtube_description_template'] = effective_youtube_description

        # Native API distribution (use effective values which include defaults)
        if dist.dropbox_path:
            update_data['dropbox_path'] = dist.dropbox_path
        if dist.gdrive_folder_id:
            update_data['gdrive_folder_id'] = dist.gdrive_folder_id
        
        # Legacy rclone distribution (deprecated)
        if organised_dir_rclone_root:
            update_data['organised_dir_rclone_root'] = organised_dir_rclone_root
        
        # Lyrics configuration
        if lyrics_artist:
            update_data['lyrics_artist'] = lyrics_artist
        if lyrics_title:
            update_data['lyrics_title'] = lyrics_title
        if lyrics_file_gcs_path:
            update_data['lyrics_file_gcs_path'] = lyrics_file_gcs_path
        if subtitle_offset_ms != 0:
            update_data['subtitle_offset_ms'] = subtitle_offset_ms
        
        # Audio separation model configuration
        if clean_instrumental_model:
            update_data['clean_instrumental_model'] = clean_instrumental_model
        if parsed_backing_vocals_models:
            update_data['backing_vocals_models'] = parsed_backing_vocals_models
        if parsed_other_stems_models:
            update_data['other_stems_models'] = parsed_other_stems_models
        
        job_manager.update_job(job_id, update_data)
        
        # Verify the update
        updated_job = job_manager.get_job(job_id)
        if not hasattr(updated_job, 'input_media_gcs_path') or not updated_job.input_media_gcs_path:
            import asyncio
            await asyncio.sleep(0.5)
            updated_job = job_manager.get_job(job_id)
            if not hasattr(updated_job, 'input_media_gcs_path') or not updated_job.input_media_gcs_path:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to update job with GCS paths"
                )
        
        logger.info(f"All files uploaded for job {job_id}")
        if style_assets:
            logger.info(f"Style assets: {list(style_assets.keys())}")
        
        # Transition job to DOWNLOADING state
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.DOWNLOADING,
            progress=5,
            message="Files uploaded, preparing to process"
        )
        
        # Trigger workers in parallel using asyncio.gather
        # (FastAPI's BackgroundTasks runs async tasks sequentially)
        background_tasks.add_task(_trigger_workers_parallel, job_id)
        
        # Build distribution services info for response
        distribution_services: Dict[str, Any] = {}

        if dist.dropbox_path:
            dropbox_result = credential_manager.check_dropbox_credentials()
            distribution_services["dropbox"] = {
                "enabled": True,
                "path": dist.dropbox_path,
                "credentials_valid": dropbox_result.status == CredentialStatus.VALID,
                "using_default": dropbox_path is None,
            }

        if dist.gdrive_folder_id:
            gdrive_result = credential_manager.check_gdrive_credentials()
            distribution_services["gdrive"] = {
                "enabled": True,
                "folder_id": dist.gdrive_folder_id,
                "credentials_valid": gdrive_result.status == CredentialStatus.VALID,
                "using_default": gdrive_folder_id is None,
            }
        
        if effective_enable_youtube_upload:
            youtube_result = credential_manager.check_youtube_credentials()
            distribution_services["youtube"] = {
                "enabled": True,
                "credentials_valid": youtube_result.status == CredentialStatus.VALID,
                "using_default": enable_youtube_upload is None,
            }
        
        if dist.discord_webhook_url:
            distribution_services["discord"] = {
                "enabled": True,
                "using_default": discord_webhook_url is None,
            }
        
        return {
            "status": "success",
            "job_id": job_id,
            "message": "Files uploaded successfully. Processing started.",
            "filename": file.filename,
            "style_assets_uploaded": list(style_assets.keys()) if style_assets else [],
            "server_version": VERSION,
            "distribution_services": distribution_services,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ============================================================================
# Signed URL Upload Flow - For large files that exceed Cloud Run's 32MB limit
# ============================================================================

# Valid file types and their expected extensions
VALID_FILE_TYPES = {
    'audio': ALLOWED_AUDIO_EXTENSIONS,
    'style_params': {'.json'},
    'style_intro_background': ALLOWED_IMAGE_EXTENSIONS,
    'style_karaoke_background': ALLOWED_IMAGE_EXTENSIONS,
    'style_end_background': ALLOWED_IMAGE_EXTENSIONS,
    'style_font': ALLOWED_FONT_EXTENSIONS,
    'style_cdg_instrumental_background': ALLOWED_IMAGE_EXTENSIONS,
    'style_cdg_title_background': ALLOWED_IMAGE_EXTENSIONS,
    'style_cdg_outro_background': ALLOWED_IMAGE_EXTENSIONS,
    'lyrics_file': {'.txt', '.docx', '.rtf'},
    'existing_instrumental': ALLOWED_AUDIO_EXTENSIONS,  # Batch 3: user-provided instrumental
}

# Valid file types for finalise-only mode (Batch 6)
ALLOWED_VIDEO_EXTENSIONS = {'.mkv', '.mov', '.mp4'}
FINALISE_ONLY_FILE_TYPES = {
    'audio': ALLOWED_AUDIO_EXTENSIONS,  # Original audio
    'with_vocals': ALLOWED_VIDEO_EXTENSIONS,  # Karaoke video from prep
    'title_screen': ALLOWED_VIDEO_EXTENSIONS,  # Title screen video
    'end_screen': ALLOWED_VIDEO_EXTENSIONS,  # End screen video
    'title_jpg': ALLOWED_IMAGE_EXTENSIONS,  # Title screen JPG
    'title_png': ALLOWED_IMAGE_EXTENSIONS,  # Title screen PNG
    'end_jpg': ALLOWED_IMAGE_EXTENSIONS,  # End screen JPG
    'end_png': ALLOWED_IMAGE_EXTENSIONS,  # End screen PNG
    'instrumental_clean': ALLOWED_AUDIO_EXTENSIONS,  # Clean instrumental
    'instrumental_backing': ALLOWED_AUDIO_EXTENSIONS,  # Instrumental with backing vocals
    'lrc': {'.lrc'},  # LRC lyrics
    'ass': {'.ass'},  # ASS subtitles
    'corrections': {'.json'},  # Corrections JSON
    'style_params': {'.json'},  # Style params
}

# Pydantic models for finalise-only flow (Batch 6)
class FinaliseOnlyFileRequest(BaseModel):
    """Information about a prep output file to be uploaded for finalise-only mode."""
    filename: str = Field(..., description="Original filename with extension")
    content_type: str = Field(..., description="MIME type of the file")
    file_type: str = Field(..., description="Type of prep file: 'with_vocals', 'title_screen', 'end_screen', 'instrumental_clean', 'instrumental_backing', 'lrc', etc.")


class CreateFinaliseOnlyJobRequest(BaseModel):
    """Request to create a finalise-only job with prep output files."""
    artist: str = Field(..., description="Artist name")
    title: str = Field(..., description="Song title")
    files: List[FinaliseOnlyFileRequest] = Field(..., description="List of prep output files to upload")

    # Theme configuration (use pre-made theme from GCS)
    theme_id: Optional[str] = Field(None, description="Theme ID to use (e.g., 'nomad', 'default'). If set, CDG/TXT are enabled by default.")
    color_overrides: Optional[Dict[str, str]] = Field(None, description="Color overrides: artist_color, title_color, sung_lyrics_color, unsung_lyrics_color (hex #RRGGBB)")

    # Processing options (CDG/TXT require style config or theme, disabled by default unless theme is set)
    enable_cdg: Optional[bool] = Field(None, description="Generate CDG+MP3 package. Default: True if theme_id set, False otherwise")
    enable_txt: Optional[bool] = Field(None, description="Generate TXT+MP3 package. Default: True if theme_id set, False otherwise")
    
    # Finalisation options
    brand_prefix: Optional[str] = Field(None, description="Brand code prefix (e.g., NOMAD)")
    keep_brand_code: Optional[str] = Field(None, description="Preserve existing brand code from folder name")
    enable_youtube_upload: Optional[bool] = Field(None, description="Upload to YouTube. None = use server default")
    youtube_description: Optional[str] = Field(None, description="YouTube video description text")
    discord_webhook_url: Optional[str] = Field(None, description="Discord webhook URL for notifications")
    
    # Distribution options
    dropbox_path: Optional[str] = Field(None, description="Dropbox folder path for organized output")
    gdrive_folder_id: Optional[str] = Field(None, description="Google Drive folder ID for public share uploads")




async def _validate_audio_durations(
    storage: StorageService,
    audio_gcs_path: str,
    instrumental_gcs_path: str,
    tolerance_seconds: float = 0.5
) -> Tuple[bool, float, float]:
    """
    Validate that audio and instrumental files have matching durations.
    
    Downloads both files to temp directory and checks their durations using pydub.
    
    Args:
        storage: StorageService instance
        audio_gcs_path: GCS path to main audio file
        instrumental_gcs_path: GCS path to instrumental file
        tolerance_seconds: Maximum allowed difference in seconds (default 0.5s)
        
    Returns:
        Tuple of (is_valid, audio_duration, instrumental_duration)
    """
    from pydub import AudioSegment
    
    temp_dir = tempfile.mkdtemp(prefix="duration_check_")
    try:
        # Download audio file
        audio_local = os.path.join(temp_dir, "audio" + Path(audio_gcs_path).suffix)
        storage.download_file(audio_gcs_path, audio_local)
        
        # Download instrumental file
        instrumental_local = os.path.join(temp_dir, "instrumental" + Path(instrumental_gcs_path).suffix)
        storage.download_file(instrumental_gcs_path, instrumental_local)
        
        # Get durations using pydub (returns milliseconds)
        audio_segment = AudioSegment.from_file(audio_local)
        audio_duration = len(audio_segment) / 1000.0  # Convert to seconds
        
        instrumental_segment = AudioSegment.from_file(instrumental_local)
        instrumental_duration = len(instrumental_segment) / 1000.0  # Convert to seconds
        
        # Check if durations are within tolerance
        difference = abs(audio_duration - instrumental_duration)
        is_valid = difference <= tolerance_seconds
        
        return is_valid, audio_duration, instrumental_duration
        
    finally:
        # Clean up temp files
        import shutil
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


def _get_gcs_path_for_file(job_id: str, file_type: str, filename: str) -> str:
    """Generate the GCS path for a file based on its type."""
    ext = Path(filename).suffix.lower()
    
    if file_type == 'audio':
        return f"uploads/{job_id}/audio/{filename}"
    elif file_type == 'style_params':
        return f"uploads/{job_id}/style/style_params.json"
    elif file_type.startswith('style_'):
        # Map style_intro_background -> intro_background, etc.
        asset_key = file_type.replace('style_', '')
        return f"uploads/{job_id}/style/{asset_key}{ext}"
    elif file_type == 'lyrics_file':
        return f"uploads/{job_id}/lyrics/user_lyrics{ext}"
    elif file_type == 'existing_instrumental':
        # Batch 3: user-provided instrumental file
        return f"uploads/{job_id}/audio/existing_instrumental{ext}"
    else:
        return f"uploads/{job_id}/other/{filename}"


@router.post("/jobs/create-with-upload-urls", response_model=CreateJobWithUploadUrlsResponse)
async def create_job_with_upload_urls(
    request: Request,
    body: CreateJobWithUploadUrlsRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Create a karaoke generation job and return signed URLs for direct file upload to GCS.
    
    This is the first step of the two-step upload flow for large files:
    1. Call this endpoint with job metadata and list of files to upload
    2. Upload each file directly to GCS using the returned signed URLs
    3. Call POST /api/jobs/{job_id}/uploads-complete to start processing
    
    Benefits of this flow:
    - No file size limits (GCS supports up to 5TB)
    - Faster uploads (direct to storage, no proxy)
    - Works with any HTTP client (no HTTP/2 required)
    - Resumable uploads possible with GCS
    """
    # Check tenant feature flag
    tenant_config = get_tenant_config_from_request(request)
    if tenant_config and not tenant_config.features.file_upload:
        raise HTTPException(
            status_code=403,
            detail="File upload is not available for this portal"
        )

    try:
        # Validate files list
        if not body.files:
            raise HTTPException(status_code=400, detail="At least one file is required")
        
        # Check that audio file is included
        audio_files = [f for f in body.files if f.file_type == 'audio']
        if not audio_files:
            raise HTTPException(status_code=400, detail="An audio file is required")
        if len(audio_files) > 1:
            raise HTTPException(status_code=400, detail="Only one audio file is allowed")
        
        # Validate file types and extensions
        for file_info in body.files:
            if file_info.file_type not in VALID_FILE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file_type: '{file_info.file_type}'. Valid types: {list(VALID_FILE_TYPES.keys())}"
                )
            
            ext = Path(file_info.filename).suffix.lower()
            allowed_extensions = VALID_FILE_TYPES[file_info.file_type]
            if ext not in allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file extension '{ext}' for file_type '{file_info.file_type}'. Allowed: {allowed_extensions}"
                )

        # Apply default distribution settings from environment if not provided
        dist = get_effective_distribution_settings(
            dropbox_path=body.dropbox_path,
            gdrive_folder_id=body.gdrive_folder_id,
            discord_webhook_url=body.discord_webhook_url,
            brand_prefix=body.brand_prefix,
        )

        # Apply YouTube upload default from settings
        # Use explicit value if provided, otherwise fall back to server default
        settings = get_settings()
        effective_enable_youtube_upload = body.enable_youtube_upload if body.enable_youtube_upload is not None else settings.default_enable_youtube_upload

        # Validate credentials for requested distribution services
        invalid_services = []
        credential_manager = get_credential_manager()

        if effective_enable_youtube_upload:
            result = credential_manager.check_youtube_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"youtube ({result.message})")

        if dist.dropbox_path:
            result = credential_manager.check_dropbox_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"dropbox ({result.message})")

        if dist.gdrive_folder_id:
            result = credential_manager.check_gdrive_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"gdrive ({result.message})")

        if invalid_services:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "credentials_invalid",
                    "message": f"The following distribution services need re-authorization: {', '.join(invalid_services)}",
                    "invalid_services": invalid_services,
                    "auth_url": "/api/auth/status"
                }
            )

        # Extract request metadata for tracking
        request_metadata = extract_request_metadata(request, created_from="signed_url_upload")

        # Get original audio filename
        audio_file = audio_files[0]

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

        # Check if style_params is being uploaded (overrides theme)
        has_style_params_upload = any(f.file_type == 'style_params' for f in body.files)

        # Prefer authenticated user's email over request body
        effective_user_email = auth_result.user_email or body.user_email

        # Create job
        job_create = JobCreate(
            artist=body.artist,
            title=body.title,
            filename=audio_file.filename,
            theme_id=effective_theme_id,
            color_overrides=body.color_overrides or {},
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            brand_prefix=dist.brand_prefix,
            enable_youtube_upload=effective_enable_youtube_upload,
            youtube_description=body.youtube_description,
            youtube_description_template=body.youtube_description,  # video_worker reads this field
            discord_webhook_url=dist.discord_webhook_url,
            webhook_url=body.webhook_url,
            user_email=effective_user_email,
            dropbox_path=dist.dropbox_path,
            gdrive_folder_id=dist.gdrive_folder_id,
            organised_dir_rclone_root=body.organised_dir_rclone_root,
            lyrics_artist=body.lyrics_artist,
            lyrics_title=body.lyrics_title,
            subtitle_offset_ms=body.subtitle_offset_ms,
            clean_instrumental_model=body.clean_instrumental_model,
            backing_vocals_models=body.backing_vocals_models,
            other_stems_models=body.other_stems_models,
            request_metadata=request_metadata,
            non_interactive=body.non_interactive,
            # Tenant scoping
            tenant_id=tenant_config.id if tenant_config else None,
        )
        job = job_manager.create_job(job_create, is_admin=auth_result.is_admin)
        job_id = job.job_id

        # Record job creation metric
        metrics.record_job_created(job_id, source="upload")

        logger.info(f"Created job {job_id} for {body.artist} - {body.title} (signed URL upload flow)")

        # If theme is set and no style_params uploaded, prepare theme style now
        if effective_theme_id and not has_style_params_upload:
            style_params_path, style_assets, youtube_desc = _prepare_theme_for_job(
                job_id, effective_theme_id, body.color_overrides
            )
            # Update job with theme style data
            update_data = {
                'style_params_gcs_path': style_params_path,
                'style_assets': style_assets,
            }
            if youtube_desc and not body.youtube_description:
                update_data['youtube_description_template'] = youtube_desc
            job_manager.update_job(job_id, update_data)
            logger.info(f"Applied theme '{effective_theme_id}' to job {job_id}")
        
        # Generate signed upload URLs for each file
        upload_urls = []
        for file_info in body.files:
            gcs_path = _get_gcs_path_for_file(job_id, file_info.file_type, file_info.filename)
            
            # Generate signed upload URL (valid for 60 minutes)
            signed_url = storage_service.generate_signed_upload_url(
                gcs_path,
                content_type=file_info.content_type,
                expiration_minutes=60
            )
            
            upload_urls.append(SignedUploadUrl(
                file_type=file_info.file_type,
                gcs_path=gcs_path,
                upload_url=signed_url,
                content_type=file_info.content_type
            ))
            
            logger.info(f"Generated signed upload URL for {file_info.file_type}: {gcs_path}")
        
        return CreateJobWithUploadUrlsResponse(
            status="success",
            job_id=job_id,
            message="Job created. Upload files to the provided URLs, then call /api/jobs/{job_id}/uploads-complete",
            upload_urls=upload_urls,
            server_version=VERSION
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating job with upload URLs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/jobs/{job_id}/uploads-complete")
async def mark_uploads_complete(
    job_id: str,
    background_tasks: BackgroundTasks,
    body: UploadsCompleteRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Mark file uploads as complete and start job processing.
    
    This is the second step of the signed URL upload flow:
    1. Create job with POST /api/jobs/create-with-upload-urls
    2. Upload files directly to GCS using signed URLs
    3. Call this endpoint to validate uploads and start processing
    
    The endpoint will:
    - Verify the job exists and is in PENDING state
    - Validate that required files (audio) were uploaded
    - Update job with GCS paths
    - Trigger audio and lyrics workers
    """
    try:
        # Get job and verify it exists
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        # Verify job is in pending state
        if job.status != JobStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Job {job_id} is not in pending state (current: {job.status}). Cannot complete uploads."
            )
        
        # Validate required files
        if 'audio' not in body.uploaded_files:
            raise HTTPException(status_code=400, detail="Audio file upload is required")
        
        # Build GCS paths for uploaded files and validate they exist
        update_data = {}
        style_assets = {}
        
        for file_type in body.uploaded_files:
            if file_type not in VALID_FILE_TYPES:
                logger.warning(f"Unknown file_type in uploaded_files: {file_type}")
                continue
            
            # Determine the GCS path - we need to find the actual file
            prefix = f"uploads/{job_id}/"
            if file_type == 'audio':
                prefix = f"uploads/{job_id}/audio/"
            elif file_type == 'style_params':
                prefix = f"uploads/{job_id}/style/style_params"
            elif file_type.startswith('style_'):
                asset_key = file_type.replace('style_', '')
                prefix = f"uploads/{job_id}/style/{asset_key}"
            elif file_type == 'lyrics_file':
                prefix = f"uploads/{job_id}/lyrics/user_lyrics"
            elif file_type == 'existing_instrumental':
                prefix = f"uploads/{job_id}/audio/existing_instrumental"
            
            # List files with this prefix to find the actual uploaded file
            files = storage_service.list_files(prefix)
            if not files:
                raise HTTPException(
                    status_code=400,
                    detail=f"File for '{file_type}' was not uploaded to GCS. Expected prefix: {prefix}"
                )
            
            # Use the first (and should be only) file found
            gcs_path = files[0]
            
            # Update appropriate field based on file type
            if file_type == 'audio':
                update_data['input_media_gcs_path'] = gcs_path
                update_data['filename'] = Path(gcs_path).name
            elif file_type == 'style_params':
                update_data['style_params_gcs_path'] = gcs_path
                style_assets['style_params'] = gcs_path
            elif file_type.startswith('style_'):
                asset_key = file_type.replace('style_', '')
                style_assets[asset_key] = gcs_path
            elif file_type == 'lyrics_file':
                update_data['lyrics_file_gcs_path'] = gcs_path
            elif file_type == 'existing_instrumental':
                update_data['existing_instrumental_gcs_path'] = gcs_path
        
        # Validate existing instrumental duration if provided (Batch 3)
        audio_gcs_path = update_data.get('input_media_gcs_path')
        instrumental_gcs_path = update_data.get('existing_instrumental_gcs_path')
        
        if audio_gcs_path and instrumental_gcs_path:
            logger.info(f"Validating instrumental duration for job {job_id}")
            try:
                duration_valid, audio_duration, instrumental_duration = await _validate_audio_durations(
                    storage_service, audio_gcs_path, instrumental_gcs_path
                )
                if not duration_valid:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "duration_mismatch",
                            "message": f"Instrumental duration ({instrumental_duration:.2f}s) does not match audio duration ({audio_duration:.2f}s). "
                                      f"Difference must be within 0.5 seconds.",
                            "audio_duration": audio_duration,
                            "instrumental_duration": instrumental_duration,
                            "difference": abs(audio_duration - instrumental_duration),
                        }
                    )
                logger.info(f"Duration validation passed: audio={audio_duration:.2f}s, instrumental={instrumental_duration:.2f}s")
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Duration validation failed with error: {e}. Proceeding without validation.")
                # Don't block the job if we can't validate - the video worker will fail more gracefully
        
        # Add style assets to update if any
        if style_assets:
            update_data['style_assets'] = style_assets
        
        # Update job with GCS paths
        job_manager.update_job(job_id, update_data)
        
        logger.info(f"Validated uploads for job {job_id}: {body.uploaded_files}")
        
        # Transition job to DOWNLOADING state
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.DOWNLOADING,
            progress=5,
            message="Files uploaded, preparing to process"
        )
        
        # Trigger workers in parallel
        background_tasks.add_task(_trigger_workers_parallel, job_id)
        
        # Get distribution services info for response
        credential_manager = get_credential_manager()
        distribution_services: Dict[str, Any] = {}
        
        # Get fresh job data
        updated_job = job_manager.get_job(job_id)
        
        if updated_job.dropbox_path:
            dropbox_result = credential_manager.check_dropbox_credentials()
            distribution_services["dropbox"] = {
                "enabled": True,
                "path": updated_job.dropbox_path,
                "credentials_valid": dropbox_result.status == CredentialStatus.VALID,
            }
        
        if updated_job.gdrive_folder_id:
            gdrive_result = credential_manager.check_gdrive_credentials()
            distribution_services["gdrive"] = {
                "enabled": True,
                "folder_id": updated_job.gdrive_folder_id,
                "credentials_valid": gdrive_result.status == CredentialStatus.VALID,
            }
        
        if updated_job.enable_youtube_upload:
            youtube_result = credential_manager.check_youtube_credentials()
            distribution_services["youtube"] = {
                "enabled": True,
                "credentials_valid": youtube_result.status == CredentialStatus.VALID,
            }
        
        if updated_job.discord_webhook_url:
            distribution_services["discord"] = {
                "enabled": True,
            }
        
        return {
            "status": "success",
            "job_id": job_id,
            "message": "Uploads validated. Processing started.",
            "files_validated": body.uploaded_files,
            "style_assets": list(style_assets.keys()) if style_assets else [],
            "server_version": VERSION,
            "distribution_services": distribution_services,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing uploads for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ============================================================================
# URL-based Job Creation - For YouTube and other online video URLs
# ============================================================================

def _validate_url(url: Optional[str]) -> bool:
    """
    Validate that a URL is a supported video/audio URL.
    
    Supports YouTube, Vimeo, SoundCloud, and other platforms supported by yt-dlp.
    
    Args:
        url: The URL to validate. Can be None or non-string.
        
    Returns:
        True if valid URL, False otherwise.
    """
    # Handle None and non-string inputs safely
    if url is None or not isinstance(url, str):
        return False
    
    # Handle empty string
    if not url:
        return False
    
    # Basic URL validation
    if not url.startswith(('http://', 'https://')):
        return False
    
    # List of known supported domains (subset - yt-dlp supports many more)
    supported_domains = [
        'youtube.com', 'youtu.be', 'www.youtube.com', 'm.youtube.com',
        'vimeo.com', 'www.vimeo.com',
        'soundcloud.com', 'www.soundcloud.com',
        'dailymotion.com', 'www.dailymotion.com',
        'twitch.tv', 'www.twitch.tv',
        'twitter.com', 'www.twitter.com', 'x.com', 'www.x.com',
        'facebook.com', 'www.facebook.com', 'fb.watch',
        'instagram.com', 'www.instagram.com',
        'tiktok.com', 'www.tiktok.com',
    ]
    
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # Remove port if present
    if ':' in domain:
        domain = domain.split(':')[0]
    
    # Check if domain matches any supported domain
    for supported in supported_domains:
        if domain == supported or domain.endswith('.' + supported):
            return True
    
    # For other URLs, let yt-dlp try (it supports many more sites)
    return True


@router.post("/jobs/create-from-url", response_model=CreateJobFromUrlResponse)
async def create_job_from_url(
    request: Request,
    background_tasks: BackgroundTasks,
    body: CreateJobFromUrlRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Create a karaoke generation job from a YouTube or other online video URL.
    
    The backend will:
    1. Validate the URL
    2. Extract metadata (artist/title) from the URL if not provided
    3. Create the job
    4. Trigger the audio worker to download and process
    
    This is an alternative to file upload for cases where the audio
    source is a YouTube video or other online content.
    
    Note: YouTube rate limiting may cause occasional download failures.
    The backend will retry automatically.
    """
    # Check tenant feature flag
    tenant_config = get_tenant_config_from_request(request)
    if tenant_config and not tenant_config.features.youtube_url:
        raise HTTPException(
            status_code=403,
            detail="URL-based job creation is not available for this portal"
        )

    try:
        # Validate URL
        if not _validate_url(body.url):
            raise HTTPException(
                status_code=400,
                detail="Invalid URL. Please provide a valid YouTube, Vimeo, SoundCloud, or other supported video URL."
            )

        # Apply default distribution settings from environment if not provided
        dist = get_effective_distribution_settings(
            dropbox_path=body.dropbox_path,
            gdrive_folder_id=body.gdrive_folder_id,
            discord_webhook_url=body.discord_webhook_url,
            brand_prefix=body.brand_prefix,
        )

        # Apply YouTube upload default from settings
        # Use explicit value if provided, otherwise fall back to server default
        settings = get_settings()
        effective_enable_youtube_upload = body.enable_youtube_upload if body.enable_youtube_upload is not None else settings.default_enable_youtube_upload

        # Validate credentials for requested distribution services
        invalid_services = []
        credential_manager = get_credential_manager()

        if effective_enable_youtube_upload:
            result = credential_manager.check_youtube_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"youtube ({result.message})")

        if dist.dropbox_path:
            result = credential_manager.check_dropbox_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"dropbox ({result.message})")

        if dist.gdrive_folder_id:
            result = credential_manager.check_gdrive_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"gdrive ({result.message})")

        if invalid_services:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "credentials_invalid",
                    "message": f"The following distribution services need re-authorization: {', '.join(invalid_services)}",
                    "invalid_services": invalid_services,
                    "auth_url": "/api/auth/status"
                }
            )

        # Extract request metadata for tracking
        request_metadata = extract_request_metadata(request, created_from="url")

        # Use provided artist/title or leave as None (will be auto-detected by audio worker)
        artist = body.artist
        title = body.title

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

        # Prefer authenticated user's email over request body
        effective_user_email = auth_result.user_email or body.user_email

        # Create job with URL
        job_create = JobCreate(
            url=body.url,
            artist=artist,
            title=title,
            filename=None,  # No file uploaded
            theme_id=effective_theme_id,
            color_overrides=body.color_overrides or {},
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            brand_prefix=dist.brand_prefix,
            enable_youtube_upload=effective_enable_youtube_upload,
            youtube_description=body.youtube_description,
            discord_webhook_url=dist.discord_webhook_url,
            webhook_url=body.webhook_url,
            user_email=effective_user_email,
            dropbox_path=dist.dropbox_path,
            gdrive_folder_id=dist.gdrive_folder_id,
            organised_dir_rclone_root=body.organised_dir_rclone_root,
            lyrics_artist=body.lyrics_artist,
            lyrics_title=body.lyrics_title,
            subtitle_offset_ms=body.subtitle_offset_ms,
            clean_instrumental_model=body.clean_instrumental_model,
            backing_vocals_models=body.backing_vocals_models,
            other_stems_models=body.other_stems_models,
            request_metadata=request_metadata,
            non_interactive=body.non_interactive,
            # Tenant scoping
            tenant_id=tenant_config.id if tenant_config else None,
        )
        job = job_manager.create_job(job_create, is_admin=auth_result.is_admin)
        job_id = job.job_id

        # Record job creation metric
        metrics.record_job_created(job_id, source="url")

        # If theme is set, prepare theme style now
        if effective_theme_id:
            style_params_path, style_assets, youtube_desc = _prepare_theme_for_job(
                job_id, effective_theme_id, body.color_overrides
            )
            # Update job with theme style data
            update_data = {
                'style_params_gcs_path': style_params_path,
                'style_assets': style_assets,
            }
            if youtube_desc and not body.youtube_description:
                update_data['youtube_description_template'] = youtube_desc
            job_manager.update_job(job_id, update_data)
            logger.info(f"Applied theme '{effective_theme_id}' to job {job_id}")

        logger.info(f"Created URL-based job {job_id} for URL: {body.url}")
        if artist:
            logger.info(f"  Artist: {artist}")
        if title:
            logger.info(f"  Title: {title}")

        # Transition job to DOWNLOADING state
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.DOWNLOADING,
            progress=5,
            message="Starting audio download from URL"
        )

        # For YouTube URLs, download audio NOW using YouTubeDownloadService
        # This uses remote flacfetch (if configured) to avoid bot detection on Cloud Run
        if _is_youtube_url(body.url):
            logger.info(f"YouTube URL detected, downloading via YouTubeDownloadService")
            try:
                youtube_service = get_youtube_download_service()
                audio_gcs_path = await youtube_service.download(
                    url=body.url,
                    job_id=job_id,
                    artist=artist,
                    title=title,
                )

                # Update job with the downloaded audio path
                job_manager.update_job(job_id, {
                    'input_media_gcs_path': audio_gcs_path,
                    'filename': os.path.basename(audio_gcs_path),
                })

                logger.info(f"YouTube audio downloaded to GCS: {audio_gcs_path}")

                # Now trigger both workers in parallel (audio already downloaded)
                job_manager.transition_to_state(
                    job_id=job_id,
                    new_status=JobStatus.DOWNLOADING,
                    progress=10,
                    message="Audio downloaded, starting processing"
                )
                background_tasks.add_task(_trigger_workers_parallel, job_id)

            except YouTubeDownloadError as e:
                logger.error(f"YouTube download failed: {e}")
                job_manager.fail_job(job_id, f"YouTube download failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"YouTube download failed: {e}"
                )
        else:
            # For non-YouTube URLs, trigger ONLY audio worker first
            # The audio worker will download the URL and trigger lyrics worker after
            background_tasks.add_task(_trigger_audio_worker_only, job_id)

        return CreateJobFromUrlResponse(
            status="success",
            job_id=job_id,
            message="Job created. Audio will be downloaded from URL.",
            detected_artist=artist,
            detected_title=title,
            server_version=VERSION
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating job from URL: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ============================================================================
# Finalise-Only Upload Flow (Batch 6)
# ============================================================================

def _get_gcs_path_for_finalise_file(job_id: str, file_type: str, filename: str) -> str:
    """Generate the GCS path for a finalise-only file based on its type."""
    ext = Path(filename).suffix.lower()
    
    # Map file types to appropriate GCS paths
    if file_type == 'audio':
        return f"uploads/{job_id}/audio/{filename}"
    elif file_type == 'with_vocals':
        return f"jobs/{job_id}/videos/with_vocals{ext}"
    elif file_type == 'title_screen':
        return f"jobs/{job_id}/screens/title{ext}"
    elif file_type == 'end_screen':
        return f"jobs/{job_id}/screens/end{ext}"
    elif file_type == 'title_jpg':
        return f"jobs/{job_id}/screens/title.jpg"
    elif file_type == 'title_png':
        return f"jobs/{job_id}/screens/title.png"
    elif file_type == 'end_jpg':
        return f"jobs/{job_id}/screens/end.jpg"
    elif file_type == 'end_png':
        return f"jobs/{job_id}/screens/end.png"
    elif file_type == 'instrumental_clean':
        return f"jobs/{job_id}/stems/instrumental_clean{ext}"
    elif file_type == 'instrumental_backing':
        return f"jobs/{job_id}/stems/instrumental_with_backing{ext}"
    elif file_type == 'lrc':
        return f"jobs/{job_id}/lyrics/karaoke.lrc"
    elif file_type == 'ass':
        return f"jobs/{job_id}/lyrics/karaoke.ass"
    elif file_type == 'corrections':
        return f"jobs/{job_id}/lyrics/corrections.json"
    elif file_type == 'style_params':
        return f"uploads/{job_id}/style/style_params.json"
    else:
        return f"uploads/{job_id}/other/{filename}"


@router.post("/jobs/create-finalise-only")
async def create_finalise_only_job(
    request: Request,
    body: CreateFinaliseOnlyJobRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Create a finalise-only job for continuing from a local prep phase.
    
    This endpoint is used when:
    1. User previously ran --prep-only which created local prep outputs
    2. User optionally made manual edits to stems, lyrics, etc.
    3. User now wants cloud to handle finalisation (encoding, distribution)
    
    Required files:
    - with_vocals: The karaoke video from prep (*.mkv or *.mov)
    - title_screen: Title screen video
    - end_screen: End screen video
    - instrumental_clean or instrumental_backing: At least one instrumental
    
    The endpoint returns signed URLs for uploading all the prep files.
    """
    # Check tenant feature flag - finalise-only requires file upload capability
    tenant_config = get_tenant_config_from_request(request)
    if tenant_config and not tenant_config.features.file_upload:
        raise HTTPException(
            status_code=403,
            detail="File upload is not available for this portal"
        )

    try:
        # Validate files list
        if not body.files:
            raise HTTPException(status_code=400, detail="At least one file is required")
        
        # Validate file types
        file_types = {f.file_type for f in body.files}
        
        # Check required files for finalise-only
        if 'with_vocals' not in file_types:
            raise HTTPException(
                status_code=400,
                detail="with_vocals video is required for finalise-only mode"
            )
        if 'title_screen' not in file_types:
            raise HTTPException(
                status_code=400,
                detail="title_screen video is required for finalise-only mode"
            )
        if 'end_screen' not in file_types:
            raise HTTPException(
                status_code=400,
                detail="end_screen video is required for finalise-only mode"
            )
        if 'instrumental_clean' not in file_types and 'instrumental_backing' not in file_types:
            raise HTTPException(
                status_code=400,
                detail="At least one instrumental (clean or backing) is required"
            )
        
        # Validate file extensions
        for file_info in body.files:
            if file_info.file_type not in FINALISE_ONLY_FILE_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file_type: '{file_info.file_type}'. Valid types: {list(FINALISE_ONLY_FILE_TYPES.keys())}"
                )
            
            ext = Path(file_info.filename).suffix.lower()
            allowed_extensions = FINALISE_ONLY_FILE_TYPES[file_info.file_type]
            if ext not in allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file extension '{ext}' for file_type '{file_info.file_type}'. Allowed: {allowed_extensions}"
                )
        
        # Apply default distribution settings
        dist = get_effective_distribution_settings(
            dropbox_path=body.dropbox_path,
            gdrive_folder_id=body.gdrive_folder_id,
            discord_webhook_url=body.discord_webhook_url,
            brand_prefix=body.brand_prefix,
        )

        # Apply YouTube upload default from settings
        # Use explicit value if provided, otherwise fall back to server default
        settings = get_settings()
        effective_enable_youtube_upload = body.enable_youtube_upload if body.enable_youtube_upload is not None else settings.default_enable_youtube_upload

        # Validate distribution credentials if services are requested
        invalid_services = []
        credential_manager = get_credential_manager()

        if effective_enable_youtube_upload:
            result = credential_manager.check_youtube_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"youtube ({result.message})")

        if dist.dropbox_path:
            result = credential_manager.check_dropbox_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"dropbox ({result.message})")

        if dist.gdrive_folder_id:
            result = credential_manager.check_gdrive_credentials()
            if result.status != CredentialStatus.VALID:
                invalid_services.append(f"gdrive ({result.message})")

        if invalid_services:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "credentials_invalid",
                    "message": f"The following distribution services need re-authorization: {', '.join(invalid_services)}",
                    "invalid_services": invalid_services,
                    "auth_url": "/api/auth/status"
                }
            )

        # Extract request metadata
        request_metadata = extract_request_metadata(request, created_from="finalise_only_upload")

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

        # Check if style_params is being uploaded (overrides theme)
        has_style_params_upload = any(f.file_type == 'style_params' for f in body.files)

        # Use authenticated user's email
        effective_user_email = auth_result.user_email

        # Create job with finalise_only=True
        job_create = JobCreate(
            artist=body.artist,
            title=body.title,
            filename="finalise_only",  # No single audio file - using prep outputs
            theme_id=effective_theme_id,
            color_overrides=body.color_overrides or {},
            enable_cdg=resolved_cdg,
            enable_txt=resolved_txt,
            brand_prefix=dist.brand_prefix,
            enable_youtube_upload=effective_enable_youtube_upload,
            youtube_description=body.youtube_description,
            discord_webhook_url=dist.discord_webhook_url,
            dropbox_path=dist.dropbox_path,
            gdrive_folder_id=dist.gdrive_folder_id,
            user_email=effective_user_email,
            finalise_only=True,
            keep_brand_code=body.keep_brand_code,
            request_metadata=request_metadata,
            # Tenant scoping
            tenant_id=tenant_config.id if tenant_config else None,
        )
        job = job_manager.create_job(job_create, is_admin=auth_result.is_admin)
        job_id = job.job_id

        # Record job creation metric
        metrics.record_job_created(job_id, source="finalise")

        logger.info(f"Created finalise-only job {job_id} for {body.artist} - {body.title}")

        # If theme is set and no style_params uploaded, prepare theme style now
        if effective_theme_id and not has_style_params_upload:
            style_params_path, style_assets, youtube_desc = _prepare_theme_for_job(
                job_id, effective_theme_id, body.color_overrides
            )
            # Update job with theme style data
            update_data = {
                'style_params_gcs_path': style_params_path,
                'style_assets': style_assets,
            }
            if youtube_desc and not body.youtube_description:
                update_data['youtube_description_template'] = youtube_desc
            job_manager.update_job(job_id, update_data)
            logger.info(f"Applied theme '{effective_theme_id}' to job {job_id}")
        
        # Generate signed upload URLs for each file
        upload_urls = []
        for file_info in body.files:
            gcs_path = _get_gcs_path_for_finalise_file(job_id, file_info.file_type, file_info.filename)
            
            # Generate signed upload URL (valid for 60 minutes)
            signed_url = storage_service.generate_signed_upload_url(
                gcs_path,
                content_type=file_info.content_type,
                expiration_minutes=60
            )
            
            upload_urls.append(SignedUploadUrl(
                file_type=file_info.file_type,
                gcs_path=gcs_path,
                upload_url=signed_url,
                content_type=file_info.content_type
            ))
            
            logger.info(f"Generated signed upload URL for {file_info.file_type}: {gcs_path}")
        
        return CreateJobWithUploadUrlsResponse(
            status="success",
            job_id=job_id,
            message="Finalise-only job created. Upload files to the provided URLs, then call /api/jobs/{job_id}/finalise-uploads-complete",
            upload_urls=upload_urls,
            server_version=VERSION
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating finalise-only job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/jobs/{job_id}/finalise-uploads-complete")
async def mark_finalise_uploads_complete(
    job_id: str,
    background_tasks: BackgroundTasks,
    body: UploadsCompleteRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Mark finalise-only file uploads as complete and start video generation.

    This is called after uploading prep outputs for a finalise-only job.
    The job will transition directly to AWAITING_INSTRUMENTAL_SELECTION.

    Note: Finalise-only jobs are a special case where users upload pre-rendered
    video and only need to select instrumental. For normal jobs, instrumental
    selection is combined with lyrics review in a single step.
    """
    try:
        # Get job and verify it exists
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        # Verify job is in pending state and is finalise-only
        if job.status != JobStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Job {job_id} is not in pending state (current: {job.status}). Cannot complete uploads."
            )
        
        if not job.finalise_only:
            raise HTTPException(
                status_code=400,
                detail=f"Job {job_id} is not a finalise-only job. Use /api/jobs/{{job_id}}/uploads-complete instead."
            )
        
        # Validate required files were uploaded
        required_files = {'with_vocals', 'title_screen', 'end_screen'}
        uploaded_set = set(body.uploaded_files)
        
        missing_files = required_files - uploaded_set
        if missing_files:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required files: {missing_files}"
            )
        
        # Require at least one instrumental
        if 'instrumental_clean' not in uploaded_set and 'instrumental_backing' not in uploaded_set:
            raise HTTPException(
                status_code=400,
                detail="At least one instrumental (clean or backing) is required"
            )
        
        # Build file_urls structure from uploaded files
        file_urls: Dict[str, Dict[str, str]] = {
            'videos': {},
            'screens': {},
            'stems': {},
            'lyrics': {},
        }
        
        for file_type in body.uploaded_files:
            if file_type not in FINALISE_ONLY_FILE_TYPES:
                logger.warning(f"Unknown file_type in uploaded_files: {file_type}")
                continue
            
            # Determine the GCS path - we need to find the actual file
            if file_type == 'audio':
                prefix = f"uploads/{job_id}/audio/"
            elif file_type == 'with_vocals':
                prefix = f"jobs/{job_id}/videos/with_vocals"
            elif file_type == 'title_screen':
                prefix = f"jobs/{job_id}/screens/title"
            elif file_type == 'end_screen':
                prefix = f"jobs/{job_id}/screens/end"
            elif file_type == 'title_jpg':
                prefix = f"jobs/{job_id}/screens/title.jpg"
            elif file_type == 'title_png':
                prefix = f"jobs/{job_id}/screens/title.png"
            elif file_type == 'end_jpg':
                prefix = f"jobs/{job_id}/screens/end.jpg"
            elif file_type == 'end_png':
                prefix = f"jobs/{job_id}/screens/end.png"
            elif file_type == 'instrumental_clean':
                prefix = f"jobs/{job_id}/stems/instrumental_clean"
            elif file_type == 'instrumental_backing':
                prefix = f"jobs/{job_id}/stems/instrumental_with_backing"
            elif file_type == 'lrc':
                prefix = f"jobs/{job_id}/lyrics/karaoke.lrc"
            elif file_type == 'ass':
                prefix = f"jobs/{job_id}/lyrics/karaoke.ass"
            elif file_type == 'corrections':
                prefix = f"jobs/{job_id}/lyrics/corrections"
            elif file_type == 'style_params':
                prefix = f"uploads/{job_id}/style/style_params"
            else:
                continue
            
            # List files with this prefix to find the actual uploaded file
            files = storage_service.list_files(prefix)
            if not files:
                raise HTTPException(
                    status_code=400,
                    detail=f"File for '{file_type}' was not uploaded to GCS. Expected prefix: {prefix}"
                )
            
            gcs_path = files[0]
            
            # Map to file_urls structure
            if file_type == 'audio':
                pass  # Handled separately
            elif file_type == 'with_vocals':
                file_urls['videos']['with_vocals'] = gcs_path
            elif file_type == 'title_screen':
                file_urls['screens']['title'] = gcs_path
            elif file_type == 'end_screen':
                file_urls['screens']['end'] = gcs_path
            elif file_type == 'title_jpg':
                file_urls['screens']['title_jpg'] = gcs_path
            elif file_type == 'title_png':
                file_urls['screens']['title_png'] = gcs_path
            elif file_type == 'end_jpg':
                file_urls['screens']['end_jpg'] = gcs_path
            elif file_type == 'end_png':
                file_urls['screens']['end_png'] = gcs_path
            elif file_type == 'instrumental_clean':
                file_urls['stems']['instrumental_clean'] = gcs_path
            elif file_type == 'instrumental_backing':
                file_urls['stems']['instrumental_with_backing'] = gcs_path
            elif file_type == 'lrc':
                file_urls['lyrics']['lrc'] = gcs_path
            elif file_type == 'ass':
                file_urls['lyrics']['ass'] = gcs_path
            elif file_type == 'corrections':
                file_urls['lyrics']['corrections'] = gcs_path
        
        # Get audio path if uploaded
        audio_gcs_path = None
        if 'audio' in body.uploaded_files:
            audio_files = storage_service.list_files(f"uploads/{job_id}/audio/")
            if audio_files:
                audio_gcs_path = audio_files[0]
        
        # Update job with file paths
        update_data = {
            'file_urls': file_urls,
            'finalise_only': True,
        }
        
        if audio_gcs_path:
            update_data['input_media_gcs_path'] = audio_gcs_path
        
        # Mark parallel processing as complete (we're skipping it)
        update_data['state_data'] = {
            'audio_complete': True,
            'lyrics_complete': True,
            'finalise_only': True,
        }
        
        job_manager.update_job(job_id, update_data)
        
        logger.info(f"Validated finalise-only uploads for job {job_id}: {body.uploaded_files}")
        
        # Transition directly to AWAITING_INSTRUMENTAL_SELECTION
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.AWAITING_INSTRUMENTAL_SELECTION,
            progress=80,
            message="Prep files uploaded - select your instrumental"
        )
        
        # Get distribution services info for response
        credential_manager = get_credential_manager()
        distribution_services: Dict[str, Any] = {}
        
        updated_job = job_manager.get_job(job_id)
        
        if updated_job.dropbox_path:
            dropbox_result = credential_manager.check_dropbox_credentials()
            distribution_services["dropbox"] = {
                "enabled": True,
                "path": updated_job.dropbox_path,
                "credentials_valid": dropbox_result.status == CredentialStatus.VALID,
            }
        
        if updated_job.gdrive_folder_id:
            gdrive_result = credential_manager.check_gdrive_credentials()
            distribution_services["gdrive"] = {
                "enabled": True,
                "folder_id": updated_job.gdrive_folder_id,
                "credentials_valid": gdrive_result.status == CredentialStatus.VALID,
            }
        
        if updated_job.enable_youtube_upload:
            youtube_result = credential_manager.check_youtube_credentials()
            distribution_services["youtube"] = {
                "enabled": True,
                "credentials_valid": youtube_result.status == CredentialStatus.VALID,
            }
        
        if updated_job.discord_webhook_url:
            distribution_services["discord"] = {
                "enabled": True,
            }
        
        return {
            "status": "success",
            "job_id": job_id,
            "message": "Finalise-only uploads validated. Select instrumental to continue.",
            "files_validated": body.uploaded_files,
            "server_version": VERSION,
            "distribution_services": distribution_services,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing finalise-only uploads for job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
