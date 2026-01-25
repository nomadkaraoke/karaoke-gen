"""
Job data models for karaoke generation.

This module defines the complete state machine for karaoke generation jobs,
mirroring the 8-stage CLI workflow with human-in-the-loop interaction points.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, validator

from karaoke_gen.utils import normalize_text


class JobStatus(str, Enum):
    """
    Job status enumeration - Complete state machine.

    The workflow has 7 main stages with 2 human interaction points:
    1. Input & Setup (may include audio source selection)
    2. Parallel Processing (audio + lyrics)
    3. Title/End Screen Generation + Backing Vocals Analysis
    4. Countdown Padding Synchronization
    5. Combined Human Review (BLOCKING) - lyrics review + instrumental selection
    6. Video Finalization
    7. Distribution

    Note: As of 2026-01, lyrics review and instrumental selection are combined
    into a single human review step. The user selects their instrumental
    during the lyrics review phase, before the video is rendered.
    """
    # Initial states
    PENDING = "pending"                           # Job created, queued for processing

    # Audio search states (for artist+title search mode)
    SEARCHING_AUDIO = "searching_audio"           # Searching for audio sources via flacfetch
    AWAITING_AUDIO_SELECTION = "awaiting_audio_selection"  # ⚠️ WAITING FOR USER - select audio source
    DOWNLOADING_AUDIO = "downloading_audio"       # Downloading selected audio from source

    DOWNLOADING = "downloading"                   # Downloading from URL or processing upload

    # Stage 2a: Audio separation (parallel track 1)
    SEPARATING_STAGE1 = "separating_stage1"      # Clean instrumental separation (Modal API)
    SEPARATING_STAGE2 = "separating_stage2"      # Backing vocals separation (Modal API)
    AUDIO_COMPLETE = "audio_complete"            # All audio stems ready

    # Stage 2b: Lyrics processing (parallel track 2)
    TRANSCRIBING = "transcribing"                # AudioShake API transcription
    CORRECTING = "correcting"                     # Automatic lyrics correction
    LYRICS_COMPLETE = "lyrics_complete"           # Corrections JSON ready

    # Stage 3: Title/End screens + Backing Vocals Analysis
    GENERATING_SCREENS = "generating_screens"     # Creating title and end screen videos

    # Stage 4: Countdown padding (automatic)
    APPLYING_PADDING = "applying_padding"         # Synchronizing countdown padding

    # Stage 5: Combined Human Review (BLOCKING) - lyrics + instrumental selection
    AWAITING_REVIEW = "awaiting_review"          # ⚠️ WAITING FOR USER - combined review needed
    IN_REVIEW = "in_review"                      # User is actively reviewing
    REVIEW_COMPLETE = "review_complete"          # User submitted lyrics + instrumental selection

    # Stage 5.5: Render video with corrected lyrics (post-review)
    RENDERING_VIDEO = "rendering_video"          # Using OutputGenerator to create with_vocals.mkv

    # Stage 6: Instrumental already selected (during combined review)
    # Note: AWAITING_INSTRUMENTAL_SELECTION kept for DB compatibility with historical jobs only
    AWAITING_INSTRUMENTAL_SELECTION = "awaiting_instrumental_selection"  # LEGACY - no longer used
    INSTRUMENTAL_SELECTED = "instrumental_selected"  # Instrumental was selected during combined review
    
    # Stage 7: Video generation and finalization
    GENERATING_VIDEO = "generating_video"        # Creating initial karaoke video
    ENCODING = "encoding"                        # Multi-format video encoding (Cloud Build)
    PACKAGING = "packaging"                      # CDG/TXT ZIP generation
    
    # Stage 8: Distribution (optional)
    UPLOADING = "uploading"                      # YouTube/Dropbox upload
    NOTIFYING = "notifying"                      # Discord/Email notifications
    
    # Terminal states
    COMPLETE = "complete"                        # All processing finished successfully
    PREP_COMPLETE = "prep_complete"             # Prep-only job completed (stops after review)
    FAILED = "failed"                           # Unrecoverable error occurred
    CANCELLED = "cancelled"                      # User cancelled the job
    
    # Legacy compatibility (will be removed)
    QUEUED = "queued"                           # Deprecated: use PENDING
    PROCESSING = "processing"                    # Deprecated: use specific states
    READY_FOR_FINALIZATION = "ready_for_finalization"  # Deprecated
    FINALIZING = "finalizing"                    # Deprecated: use ENCODING/PACKAGING
    ERROR = "error"                             # Deprecated: use FAILED


# Valid state transitions
STATE_TRANSITIONS = {
    # PENDING can go to DOWNLOADING (file upload) or SEARCHING_AUDIO (artist+title search)
    JobStatus.PENDING: [JobStatus.DOWNLOADING, JobStatus.SEARCHING_AUDIO, JobStatus.FAILED, JobStatus.CANCELLED],

    # Audio search flow (for artist+title search mode)
    JobStatus.SEARCHING_AUDIO: [JobStatus.AWAITING_AUDIO_SELECTION, JobStatus.DOWNLOADING_AUDIO, JobStatus.FAILED],
    JobStatus.AWAITING_AUDIO_SELECTION: [JobStatus.DOWNLOADING_AUDIO, JobStatus.FAILED, JobStatus.CANCELLED],
    JobStatus.DOWNLOADING_AUDIO: [JobStatus.DOWNLOADING, JobStatus.FAILED],

    # DOWNLOADING allows parallel processing (audio + lyrics) and then screens when both complete
    JobStatus.DOWNLOADING: [JobStatus.SEPARATING_STAGE1, JobStatus.TRANSCRIBING, JobStatus.GENERATING_SCREENS, JobStatus.FAILED],

    # Audio separation flow
    JobStatus.SEPARATING_STAGE1: [JobStatus.SEPARATING_STAGE2, JobStatus.FAILED],
    JobStatus.SEPARATING_STAGE2: [JobStatus.AUDIO_COMPLETE, JobStatus.FAILED],
    JobStatus.AUDIO_COMPLETE: [JobStatus.GENERATING_SCREENS, JobStatus.FAILED],

    # Lyrics flow
    JobStatus.TRANSCRIBING: [JobStatus.CORRECTING, JobStatus.FAILED],
    JobStatus.CORRECTING: [JobStatus.LYRICS_COMPLETE, JobStatus.FAILED],
    JobStatus.LYRICS_COMPLETE: [JobStatus.GENERATING_SCREENS, JobStatus.FAILED],

    # Post-parallel processing (screens + backing vocals analysis)
    JobStatus.GENERATING_SCREENS: [JobStatus.APPLYING_PADDING, JobStatus.AWAITING_REVIEW, JobStatus.FAILED],
    JobStatus.APPLYING_PADDING: [JobStatus.AWAITING_REVIEW, JobStatus.FAILED],

    # Combined human review flow (lyrics + instrumental selection)
    # AWAITING_REVIEW can go directly to REVIEW_COMPLETE (quick review) or to IN_REVIEW (editing)
    JobStatus.AWAITING_REVIEW: [JobStatus.IN_REVIEW, JobStatus.REVIEW_COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED],
    JobStatus.IN_REVIEW: [JobStatus.REVIEW_COMPLETE, JobStatus.AWAITING_REVIEW, JobStatus.FAILED],
    JobStatus.REVIEW_COMPLETE: [JobStatus.RENDERING_VIDEO, JobStatus.PREP_COMPLETE, JobStatus.FAILED],  # PREP_COMPLETE for prep-only jobs

    # Video rendering (post-review) - instrumental was already selected during combined review
    JobStatus.RENDERING_VIDEO: [JobStatus.INSTRUMENTAL_SELECTED, JobStatus.PREP_COMPLETE, JobStatus.FAILED],

    # AWAITING_INSTRUMENTAL_SELECTION is LEGACY - kept for historical jobs in DB
    # New jobs never enter this state; they go directly to INSTRUMENTAL_SELECTED after render
    JobStatus.AWAITING_INSTRUMENTAL_SELECTION: [JobStatus.INSTRUMENTAL_SELECTED, JobStatus.FAILED, JobStatus.CANCELLED],
    JobStatus.INSTRUMENTAL_SELECTED: [JobStatus.GENERATING_VIDEO, JobStatus.FAILED],

    # Video generation flow
    JobStatus.GENERATING_VIDEO: [JobStatus.ENCODING, JobStatus.FAILED],
    JobStatus.ENCODING: [JobStatus.PACKAGING, JobStatus.COMPLETE, JobStatus.FAILED],
    JobStatus.PACKAGING: [JobStatus.UPLOADING, JobStatus.COMPLETE, JobStatus.FAILED],

    # Distribution flow
    JobStatus.UPLOADING: [JobStatus.NOTIFYING, JobStatus.COMPLETE, JobStatus.FAILED],
    JobStatus.NOTIFYING: [JobStatus.COMPLETE, JobStatus.FAILED],

    # Terminal states - COMPLETE, PREP_COMPLETE have no transitions
    # FAILED and CANCELLED allow retry transitions to resume from checkpoints
    # PREP_COMPLETE allows continuation from combined review
    JobStatus.COMPLETE: [],
    JobStatus.PREP_COMPLETE: [JobStatus.AWAITING_REVIEW, JobStatus.FAILED],  # Continue from combined review
    JobStatus.FAILED: [
        JobStatus.DOWNLOADING,            # Retry from beginning (if input audio exists)
        JobStatus.INSTRUMENTAL_SELECTED,  # Retry from video generation
        JobStatus.REVIEW_COMPLETE,        # Retry from render stage
        JobStatus.LYRICS_COMPLETE,        # Retry from screens generation
        JobStatus.AWAITING_REVIEW,        # Retry from combined review
    ],
    JobStatus.CANCELLED: [
        JobStatus.DOWNLOADING,            # Retry from beginning (if input audio exists)
        JobStatus.INSTRUMENTAL_SELECTED,  # Retry from video generation
        JobStatus.REVIEW_COMPLETE,        # Retry from render stage
        JobStatus.LYRICS_COMPLETE,        # Retry from screens generation
        JobStatus.AWAITING_REVIEW,        # Retry from combined review
    ],

    # Legacy states (for backward compatibility)
    JobStatus.QUEUED: [JobStatus.PENDING],
    JobStatus.PROCESSING: [JobStatus.SEPARATING_STAGE1, JobStatus.TRANSCRIBING],
    JobStatus.READY_FOR_FINALIZATION: [JobStatus.GENERATING_VIDEO],
    JobStatus.FINALIZING: [JobStatus.ENCODING],
    JobStatus.ERROR: [JobStatus.FAILED],
}


class TimelineEvent(BaseModel):
    """Timeline event for job progress tracking."""
    status: str
    timestamp: str
    progress: Optional[int] = None
    message: Optional[str] = None


class LogEntry(BaseModel):
    """Worker log entry for debugging and monitoring."""
    timestamp: str
    level: str  # DEBUG, INFO, WARNING, ERROR
    worker: str  # audio, lyrics, screens, video, render
    message: str


class Job(BaseModel):
    """
    Complete job data model.
    
    Tracks the full lifecycle of a karaoke generation job from submission
    through all 8 stages to completion or failure.
    """
    job_id: str
    status: JobStatus
    progress: int = 0  # 0-100 percentage for UI display
    created_at: datetime
    updated_at: datetime
    
    # Input data
    url: Optional[str] = None                    # YouTube URL (if provided)
    artist: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None               # Original uploaded filename
    input_media_gcs_path: Optional[str] = None   # GCS path to uploaded file
    
    # User preferences
    enable_cdg: bool = False                     # Generate CDG+MP3 package (requires style config)
    enable_txt: bool = False                     # Generate TXT+MP3 package (requires style config)
    enable_youtube_upload: bool = False          # Upload to YouTube
    youtube_description: Optional[str] = None    # YouTube video description
    webhook_url: Optional[str] = None            # Webhook for notifications
    user_email: Optional[str] = None             # Email for notifications
    non_interactive: bool = False                # Skip interactive steps (lyrics review, instrumental selection)

    # Multi-tenant support (None = default Nomad Karaoke)
    tenant_id: Optional[str] = None              # Tenant ID for white-label portal scoping
    
    # Theme configuration (pre-made themes from GCS)
    theme_id: Optional[str] = None               # Theme identifier (e.g., "nomad", "default")
    color_overrides: Dict[str, str] = Field(default_factory=dict)
    """
    User color overrides applied on top of theme. Keys:
    - artist_color: Hex color for artist name (#RRGGBB)
    - title_color: Hex color for song title
    - sung_lyrics_color: Hex color for highlighted lyrics
    - unsung_lyrics_color: Hex color for unhighlighted lyrics
    """

    # Style configuration (uploaded files - used when theme_id is not set)
    style_params_gcs_path: Optional[str] = None  # GCS path to style_params.json
    style_assets: Dict[str, str] = Field(default_factory=dict)
    """
    GCS paths to style asset files:
    {
        "intro_background": "gs://bucket/jobs/{job_id}/style/intro_bg.png",
        "karaoke_background": "gs://bucket/jobs/{job_id}/style/karaoke_bg.png",
        "end_background": "gs://bucket/jobs/{job_id}/style/end_bg.png",
        "font": "gs://bucket/jobs/{job_id}/style/font.ttf",
        "cdg_instrumental_background": "gs://bucket/jobs/{job_id}/style/cdg_instr.png",
        "cdg_title_background": "gs://bucket/jobs/{job_id}/style/cdg_title.png",
        "cdg_outro_background": "gs://bucket/jobs/{job_id}/style/cdg_outro.png"
    }
    """
    
    # Finalisation configuration
    brand_prefix: Optional[str] = None           # Brand code prefix (e.g., "NOMAD")
    discord_webhook_url: Optional[str] = None    # Discord notification webhook
    youtube_description_template: Optional[str] = None  # YouTube description template text
    
    # Distribution configuration (native API - for remote CLI)
    dropbox_path: Optional[str] = None           # Dropbox folder path for organized output (e.g., "/Karaoke/Tracks-Organized")
    gdrive_folder_id: Optional[str] = None       # Google Drive folder ID for public share uploads
    
    # Legacy distribution configuration (rclone - for local CLI backward compat)
    organised_dir_rclone_root: Optional[str] = None  # Deprecated: use dropbox_path instead
    
    # Lyrics configuration (overrides for search/transcription)
    lyrics_artist: Optional[str] = None          # Override artist name for lyrics search
    lyrics_title: Optional[str] = None           # Override title for lyrics search
    lyrics_file_gcs_path: Optional[str] = None   # GCS path to user-provided lyrics file
    subtitle_offset_ms: int = 0                  # Offset for subtitle timing (positive = delay)
    
    # Audio separation model configuration
    clean_instrumental_model: Optional[str] = None   # Model for clean instrumental separation
    backing_vocals_models: Optional[List[str]] = None  # Models for backing vocals separation
    other_stems_models: Optional[List[str]] = None     # Models for other stems (bass, drums, etc.)
    
    # Existing instrumental configuration (Batch 3)
    existing_instrumental_gcs_path: Optional[str] = None  # GCS path to user-provided instrumental file
    
    # Audio search configuration (Batch 5 - artist+title search mode)
    audio_search_artist: Optional[str] = None     # Artist name used for audio search
    audio_search_title: Optional[str] = None      # Title used for audio search
    auto_download: bool = False                    # Auto-select best audio source (skip selection)
    
    # Two-phase workflow configuration (Batch 6)
    prep_only: bool = False                      # Stop after review, don't run finalisation
    finalise_only: bool = False                  # Skip prep, run only finalisation (requires uploaded prep outputs)
    keep_brand_code: Optional[str] = None        # Preserve existing brand code instead of generating new one
    
    # Review authentication (Batch 7)
    review_token: Optional[str] = None           # Job-scoped token for lyrics review UI access (generated when entering AWAITING_REVIEW)
    review_token_expires_at: Optional[datetime] = None  # Token expiry time (optional, for extra security)
    instrumental_token: Optional[str] = None     # Job-scoped token for instrumental review UI access (generated when entering AWAITING_INSTRUMENTAL_SELECTION)
    instrumental_token_expires_at: Optional[datetime] = None  # Token expiry time

    # Made-for-you order tracking
    made_for_you: bool = False                   # Flag indicating this is a made-for-you customer order
    customer_email: Optional[str] = None         # Customer email for final delivery (job owned by admin during processing)
    customer_notes: Optional[str] = None         # Notes provided by customer with their order

    # Output deletion tracking (for admin cleanup without deleting job)
    outputs_deleted_at: Optional[datetime] = None  # Timestamp when outputs were deleted by admin
    outputs_deleted_by: Optional[str] = None       # Admin email who deleted outputs

    # Processing state
    track_output_dir: Optional[str] = None       # Local output directory (temp)
    audio_hash: Optional[str] = None             # Hash for deduplication
    
    # State-specific data (JSON field for stage-specific metadata)
    state_data: Dict[str, Any] = Field(default_factory=dict)
    """
    Stage-specific metadata. Examples:
    - audio_complete: {"stems": {"clean": "gs://...", "backing": "gs://..."}}
    - lyrics_complete: {"corrections_url": "gs://...", "audio_url": "gs://..."}
    - review_complete: {"corrected_lyrics": {...}}
    - instrumental_selected: {"selection": "clean" | "with_backing"}
    - encoding: {"build_id": "...", "progress": 45}
    """
    
    # Timeline tracking
    timeline: List[TimelineEvent] = Field(default_factory=list)
    
    # Worker logs for debugging (limited to last N entries to avoid document size issues)
    worker_logs: List[LogEntry] = Field(default_factory=list)
    
    # File URLs (GCS storage)
    file_urls: Dict[str, Any] = Field(default_factory=dict)
    """
    File storage URLs. Structure:
    {
        "input": "gs://bucket/jobs/{job_id}/input.flac",
        "stems": {
            "instrumental_clean": "gs://...",
            "instrumental_with_backing": "gs://...",
            "vocals": "gs://...",
            "backing_vocals": "gs://...",
            "lead_vocals": "gs://...",
            "bass": "gs://...",
            "drums": "gs://...",
            "guitar": "gs://...",
            "piano": "gs://...",
            "other": "gs://..."
        },
        "lyrics": {
            "corrections": "gs://bucket/jobs/{job_id}/lyrics/corrections.json",
            "audio": "gs://bucket/jobs/{job_id}/lyrics/audio.flac",
            "lrc": "gs://...",
            "ass": "gs://..."
        },
        "screens": {
            "title": "gs://bucket/jobs/{job_id}/screens/title.mov",
            "end": "gs://bucket/jobs/{job_id}/screens/end.mov"
        },
        "videos": {
            "with_vocals": "gs://bucket/jobs/{job_id}/videos/with_vocals.mkv"
        },
        "finals": {
            "lossless_4k_mp4": "gs://...",
            "lossless_4k_mkv": "gs://...",
            "lossy_4k_mp4": "gs://...",
            "lossy_720p_mp4": "gs://..."
        },
        "packages": {
            "cdg_zip": "gs://...",
            "txt_zip": "gs://..."
        },
        "youtube": {
            "url": "https://youtube.com/watch?v=...",
            "video_id": "..."
        }
    }
    """
    
    # Results (for backward compatibility, will be deprecated)
    output_files: Dict[str, str] = Field(default_factory=dict)
    download_urls: Dict[str, str] = Field(default_factory=dict)
    
    # Error handling
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None  # Structured error information
    retry_count: int = 0                            # Number of retry attempts
    
    # Worker tracking
    worker_ids: Dict[str, str] = Field(default_factory=dict)
    """
    IDs of background workers/jobs:
    {
        "audio_worker": "cloud-run-request-id",
        "lyrics_worker": "cloud-run-request-id",
        "video_encoder": "cloud-build-id",
        "distribution": "cloud-run-request-id"
    }
    """
    
    # Request metadata (captured at job creation for tracking and filtering)
    request_metadata: Dict[str, Any] = Field(default_factory=dict)
    """
    Metadata captured from the original API request.
    Used for tracking, filtering, and operational management.
    
    Standard fields:
    {
        "client_ip": "192.168.1.1",           # IP address of the client
        "user_agent": "karaoke-gen-remote/0.71.0",  # User-Agent header
        "environment": "test",                 # From X-Environment header (test/production/development)
        "client_id": "cli-user-123",          # From X-Client-ID header (customer/user identifier)
        "server_version": "0.71.0",           # Server version at job creation
        "created_from": "upload",              # "upload" (file) or "url" (YouTube URL)
        "custom_headers": {                    # All X-* headers for extensibility
            "X-Environment": "test",
            "X-Client-ID": "cli-user-123",
            "X-Request-ID": "abc-123"
        }
    }
    
    Use cases:
    - Filter test vs production jobs
    - Track jobs by customer/client
    - Debug issues with specific clients
    - Bulk cleanup of test jobs
    """
    
    # Note: Status transition validation is handled by JobManager.validate_state_transition()
    # which is called before status updates. The Job model does not validate transitions
    # because Firestore updates happen directly without reconstructing the model.
    
    class Config:
        use_enum_values = True


class JobCreate(BaseModel):
    """
    Job creation request.
    
    Either `url` OR file upload is required (file upload handled separately).
    Artist and title are optional - will be auto-detected from YouTube if not provided.
    """
    url: Optional[str] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None  # Original uploaded filename

    # Optional preferences
    enable_cdg: bool = False  # Requires style config
    enable_txt: bool = False  # Requires style config
    enable_youtube_upload: bool = False
    youtube_description: Optional[str] = None
    webhook_url: Optional[str] = None
    user_email: Optional[str] = None
    non_interactive: bool = False  # Skip interactive steps (lyrics review, instrumental selection)
    
    # Theme configuration (pre-made themes from GCS)
    theme_id: Optional[str] = None               # Theme identifier (e.g., "nomad", "default")
    color_overrides: Dict[str, str] = Field(default_factory=dict)
    """
    User color overrides applied on top of theme. Keys:
    - artist_color: Hex color for artist name (#RRGGBB)
    - title_color: Hex color for song title
    - sung_lyrics_color: Hex color for highlighted lyrics
    - unsung_lyrics_color: Hex color for unhighlighted lyrics
    """

    # Style configuration (will be populated after file upload, or from theme)
    style_params_gcs_path: Optional[str] = None
    style_assets: Dict[str, str] = Field(default_factory=dict)
    
    # Finalisation configuration
    brand_prefix: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    youtube_description_template: Optional[str] = None
    
    # Distribution configuration (native API - for remote CLI)
    dropbox_path: Optional[str] = None           # Dropbox folder path for organized output
    gdrive_folder_id: Optional[str] = None       # Google Drive folder ID for public share uploads
    
    # Legacy (rclone - deprecated, use dropbox_path instead)
    organised_dir_rclone_root: Optional[str] = None
    
    # Lyrics configuration (overrides for search/transcription)
    lyrics_artist: Optional[str] = None          # Override artist name for lyrics search
    lyrics_title: Optional[str] = None           # Override title for lyrics search
    lyrics_file_gcs_path: Optional[str] = None   # GCS path to user-provided lyrics file
    subtitle_offset_ms: int = 0                  # Offset for subtitle timing (positive = delay)
    
    # Audio separation model configuration
    clean_instrumental_model: Optional[str] = None   # Model for clean instrumental separation
    backing_vocals_models: Optional[List[str]] = None  # Models for backing vocals separation
    other_stems_models: Optional[List[str]] = None     # Models for other stems (bass, drums, etc.)
    
    # Existing instrumental configuration (Batch 3)
    existing_instrumental_gcs_path: Optional[str] = None  # GCS path to user-provided instrumental file
    
    # Audio search configuration (Batch 5 - artist+title search mode)
    audio_search_artist: Optional[str] = None     # Artist name used for audio search
    audio_search_title: Optional[str] = None      # Title used for audio search
    auto_download: bool = False                    # Auto-select best audio source (skip selection)
    
    # Two-phase workflow configuration (Batch 6)
    prep_only: bool = False                      # Stop after review, don't run finalisation
    finalise_only: bool = False                  # Skip prep, run only finalisation
    keep_brand_code: Optional[str] = None        # Preserve existing brand code instead of generating new one

    # Made-for-you order tracking
    made_for_you: bool = False                   # Flag indicating this is a made-for-you customer order
    customer_email: Optional[str] = None         # Customer email for final delivery (job owned by admin during processing)
    customer_notes: Optional[str] = None         # Notes provided by customer with their order

    # Request metadata (set by API endpoint from request headers)
    request_metadata: Dict[str, Any] = Field(default_factory=dict)
    """
    Populated by the API endpoint with request context:
    - client_ip: Client IP address
    - user_agent: User-Agent header
    - environment: From X-Environment header (test/production/development)
    - client_id: From X-Client-ID header
    - server_version: Current server version
    - custom_headers: All X-* headers
    """

    # Tenant scoping for white-label portals
    tenant_id: Optional[str] = None              # Tenant ID for job scoping

    @validator('url')
    def validate_url(cls, v):
        """Validate URL is not empty."""
        if v is not None and isinstance(v, str) and not v.strip():
            raise ValueError("Field cannot be empty string")
        return v.strip() if isinstance(v, str) else v

    @validator('artist', 'title')
    def normalize_artist_title(cls, v):
        """Normalize artist/title text to standardize Unicode characters.

        This ensures consistent data storage by converting:
        - Curly quotes -> straight quotes
        - Various dashes -> hyphen
        - Unusual whitespace -> regular space
        """
        if v is not None and isinstance(v, str):
            if not v.strip():
                raise ValueError("Field cannot be empty string")
            # normalize_text handles stripping and Unicode normalization
            return normalize_text(v)
        return v


class JobResponse(BaseModel):
    """Job response model."""
    status: str
    job_id: str
    message: str

