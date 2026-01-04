"""
Video Worker Orchestrator.

Coordinates the video generation pipeline stages in a unified way,
regardless of whether encoding happens locally or on GCE.

This resolves the code path divergence where GCE encoding bypassed
features like YouTube upload, Discord notifications, and CDG/TXT packaging.

Pipeline stages:
1. Setup - Download files, prepare directories
2. Packaging - CDG/TXT generation (if enabled)
3. Encoding - GCE or Local via EncodingBackend interface
4. Organization - Brand code, folder structure
5. Distribution - YouTube, Dropbox, Google Drive uploads
6. Notification - Discord notifications
"""

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from pathlib import Path

from backend.models.job import JobStatus
from backend.services.job_manager import JobManager
from backend.services.storage_service import StorageService
from backend.services.tracing import job_span, add_span_event

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    """Configuration for the video worker orchestrator."""
    job_id: str
    artist: str
    title: str

    # Input file paths (in temp_dir)
    title_video_path: str
    karaoke_video_path: str
    instrumental_audio_path: str
    end_video_path: Optional[str] = None
    lrc_file_path: Optional[str] = None
    title_jpg_path: Optional[str] = None

    # Output directory
    output_dir: str = ""

    # Feature flags
    enable_cdg: bool = False
    enable_txt: bool = False
    enable_youtube_upload: bool = False

    # Service configurations
    brand_prefix: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    youtube_credentials: Optional[Dict[str, Any]] = None
    youtube_description_template: Optional[str] = None
    cdg_styles: Optional[Dict[str, Any]] = None

    # Dropbox/GDrive configuration
    dropbox_path: Optional[str] = None
    gdrive_folder_id: Optional[str] = None

    # Keep existing brand code (for re-processing)
    keep_brand_code: Optional[str] = None

    # Encoding backend preference
    encoding_backend: str = "auto"  # "auto", "local", "gce"

    # Additional options
    dry_run: bool = False
    non_interactive: bool = True


@dataclass
class OrchestratorResult:
    """Result from the video worker orchestrator."""
    success: bool
    error_message: Optional[str] = None

    # Generated files
    final_video: Optional[str] = None  # Lossless 4K MP4
    final_video_mkv: Optional[str] = None  # Lossless 4K MKV
    final_video_lossy: Optional[str] = None  # Lossy 4K MP4
    final_video_720p: Optional[str] = None  # Lossy 720p MP4
    final_karaoke_cdg_zip: Optional[str] = None
    final_karaoke_txt_zip: Optional[str] = None

    # Organization
    brand_code: Optional[str] = None

    # Distribution results
    youtube_url: Optional[str] = None
    dropbox_link: Optional[str] = None
    gdrive_files: Optional[Dict[str, str]] = field(default_factory=dict)

    # Timing
    encoding_time_seconds: Optional[float] = None
    total_time_seconds: Optional[float] = None


class VideoWorkerOrchestrator:
    """
    Orchestrates the video generation pipeline.

    This class coordinates all stages of video generation in a unified way,
    ensuring that features like YouTube upload and Discord notifications
    work regardless of whether GCE or local encoding is used.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        job_manager: Optional[JobManager] = None,
        storage: Optional[StorageService] = None,
        job_logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            config: Orchestrator configuration
            job_manager: Job manager for state updates (optional)
            storage: Storage service for file downloads (optional)
            job_logger: Job-specific logger (optional)
        """
        self.config = config
        self.job_manager = job_manager
        self.storage = storage
        self.job_log = job_logger or logging.getLogger(__name__)

        # Services (lazy-loaded)
        self._encoding_backend = None
        self._packaging_service = None
        self._youtube_service = None
        self._discord_service = None

        # State
        self.result = OrchestratorResult(success=False)

    def _get_encoding_backend(self):
        """Lazy-load the encoding backend."""
        if self._encoding_backend is None:
            from backend.services.encoding_interface import get_encoding_backend
            self._encoding_backend = get_encoding_backend(
                backend_type=self.config.encoding_backend,
                dry_run=self.config.dry_run,
                logger=self.job_log,
            )
        return self._encoding_backend

    def _get_packaging_service(self):
        """Lazy-load the packaging service."""
        if self._packaging_service is None:
            from backend.services.packaging_service import PackagingService
            self._packaging_service = PackagingService(
                cdg_styles=self.config.cdg_styles,
                dry_run=self.config.dry_run,
                non_interactive=self.config.non_interactive,
                logger=self.job_log,
            )
        return self._packaging_service

    def _get_youtube_service(self):
        """Lazy-load the YouTube upload service."""
        if self._youtube_service is None:
            from backend.services.youtube_upload_service import YouTubeUploadService
            self._youtube_service = YouTubeUploadService(
                credentials=self.config.youtube_credentials,
                non_interactive=self.config.non_interactive,
                server_side_mode=True,
                dry_run=self.config.dry_run,
                logger=self.job_log,
            )
        return self._youtube_service

    def _get_discord_service(self):
        """Lazy-load the Discord notification service."""
        if self._discord_service is None:
            from backend.services.discord_service import DiscordNotificationService
            self._discord_service = DiscordNotificationService(
                webhook_url=self.config.discord_webhook_url,
                dry_run=self.config.dry_run,
                logger=self.job_log,
            )
        return self._discord_service

    def _update_progress(self, status: JobStatus, progress: int, message: str):
        """Update job progress if job_manager is available."""
        if self.job_manager:
            self.job_manager.transition_to_state(
                job_id=self.config.job_id,
                new_status=status,
                progress=progress,
                message=message
            )

    async def run(self) -> OrchestratorResult:
        """
        Run the full video generation pipeline.

        Returns:
            OrchestratorResult with generated files and metadata
        """
        start_time = time.time()

        try:
            with job_span("orchestrator", self.config.job_id) as span:
                self.job_log.info(f"Starting orchestrated video generation for {self.config.artist} - {self.config.title}")

                # Stage 1: Packaging (CDG/TXT) - runs BEFORE encoding
                # This depends on LRC file, not on encoded videos
                if self.config.enable_cdg or self.config.enable_txt:
                    await self._run_packaging()

                # Stage 2: Encoding
                await self._run_encoding()

                # Stage 3: Organization (brand code)
                await self._run_organization()

                # Stage 4: Distribution (YouTube, Dropbox, GDrive)
                await self._run_distribution()

                # Stage 5: Notifications (Discord)
                await self._run_notifications()

                self.result.success = True
                self.result.total_time_seconds = time.time() - start_time

                self.job_log.info(f"Orchestrated video generation complete in {self.result.total_time_seconds:.1f}s")

        except Exception as e:
            self.result.success = False
            self.result.error_message = str(e)
            self.result.total_time_seconds = time.time() - start_time
            self.job_log.error(f"Orchestrated video generation failed: {e}")
            logger.error(f"[job:{self.config.job_id}] Orchestration failed: {e}")

        return self.result

    async def _run_packaging(self):
        """Run the packaging stage (CDG/TXT generation)."""
        self.job_log.info("Starting packaging stage (CDG/TXT)")

        if not self.config.lrc_file_path or not os.path.isfile(self.config.lrc_file_path):
            self.job_log.warning("No LRC file available, skipping CDG/TXT packaging")
            return

        base_name = f"{self.config.artist} - {self.config.title}"
        packaging_service = self._get_packaging_service()

        # Generate CDG package
        if self.config.enable_cdg:
            self.job_log.info("Generating CDG package")
            try:
                cdg_zip_path = os.path.join(
                    self.config.output_dir,
                    f"{base_name} (Final Karaoke CDG).zip"
                )
                mp3_path = os.path.join(
                    self.config.output_dir,
                    f"{base_name} (Karaoke).mp3"
                )
                cdg_path = os.path.join(
                    self.config.output_dir,
                    f"{base_name} (Karaoke).cdg"
                )

                zip_file, mp3_file, cdg_file = packaging_service.create_cdg_package(
                    lrc_file=self.config.lrc_file_path,
                    audio_file=self.config.instrumental_audio_path,
                    output_zip_path=cdg_zip_path,
                    artist=self.config.artist,
                    title=self.config.title,
                    output_mp3_path=mp3_path,
                    output_cdg_path=cdg_path,
                )

                self.result.final_karaoke_cdg_zip = zip_file
                self.job_log.info(f"CDG package created: {zip_file}")

            except Exception as e:
                self.job_log.error(f"CDG generation failed: {e}")
                # Don't fail the pipeline, CDG is optional

        # Generate TXT package
        if self.config.enable_txt:
            self.job_log.info("Generating TXT package")
            try:
                # TXT package needs MP3 file (from CDG generation or create it)
                mp3_path = os.path.join(
                    self.config.output_dir,
                    f"{base_name} (Karaoke).mp3"
                )

                if not os.path.isfile(mp3_path):
                    self.job_log.info("MP3 not found, CDG must be enabled first for TXT")
                    return

                txt_zip_path = os.path.join(
                    self.config.output_dir,
                    f"{base_name} (Final Karaoke TXT).zip"
                )

                zip_file, txt_file = packaging_service.create_txt_package(
                    lrc_file=self.config.lrc_file_path,
                    mp3_file=mp3_path,
                    output_zip_path=txt_zip_path,
                )

                self.result.final_karaoke_txt_zip = zip_file
                self.job_log.info(f"TXT package created: {zip_file}")

            except Exception as e:
                self.job_log.error(f"TXT generation failed: {e}")
                # Don't fail the pipeline, TXT is optional

    async def _run_encoding(self):
        """Run the encoding stage."""
        self.job_log.info("Starting encoding stage")
        self._update_progress(JobStatus.ENCODING, 75, "Encoding videos")

        encoding_backend = self._get_encoding_backend()
        self.job_log.info(f"Using encoding backend: {encoding_backend.name}")

        # Build encoding input
        from backend.services.encoding_interface import EncodingInput
        from backend.config import settings

        # For GCE encoding, we need to provide GCS paths
        gcs_bucket = settings.gcs_bucket_name
        input_gcs_path = f"gs://{gcs_bucket}/jobs/{self.config.job_id}/"
        output_gcs_path = f"gs://{gcs_bucket}/jobs/{self.config.job_id}/finals/"

        encoding_input = EncodingInput(
            title_video_path=self.config.title_video_path,
            karaoke_video_path=self.config.karaoke_video_path,
            instrumental_audio_path=self.config.instrumental_audio_path,
            end_video_path=self.config.end_video_path,
            artist=self.config.artist,
            title=self.config.title,
            brand_code=self.config.keep_brand_code,
            output_dir=self.config.output_dir,
            options={
                "input_gcs_path": input_gcs_path,
                "output_gcs_path": output_gcs_path,
            },
        )

        # Run encoding
        with job_span("encoding", self.config.job_id) as span:
            add_span_event("encoding_started", {"backend": encoding_backend.name})

            output = await encoding_backend.encode(encoding_input)

            add_span_event("encoding_completed", {
                "success": output.success,
                "duration": output.encoding_time_seconds or 0
            })

        if not output.success:
            raise Exception(f"Encoding failed: {output.error_message}")

        # Store results
        self.result.final_video = output.lossless_4k_mp4_path
        self.result.final_video_mkv = output.lossless_mkv_path
        self.result.final_video_lossy = output.lossy_4k_mp4_path
        self.result.final_video_720p = output.lossy_720p_mp4_path
        self.result.encoding_time_seconds = output.encoding_time_seconds

        self.job_log.info(f"Encoding complete ({encoding_backend.name}) in {output.encoding_time_seconds:.1f}s")

    async def _run_organization(self):
        """Run the organization stage (brand code generation)."""
        self.job_log.info("Starting organization stage")

        # Use existing brand code if provided
        if self.config.keep_brand_code:
            self.result.brand_code = self.config.keep_brand_code
            self.job_log.info(f"Using preserved brand code: {self.result.brand_code}")
            return

        # Generate brand code from Dropbox if configured
        if self.config.dropbox_path and self.config.brand_prefix:
            try:
                from backend.services.dropbox_service import get_dropbox_service

                dropbox = get_dropbox_service()
                if dropbox.is_configured:
                    brand_code = dropbox.get_next_brand_code(
                        self.config.dropbox_path,
                        self.config.brand_prefix
                    )
                    self.result.brand_code = brand_code
                    self.job_log.info(f"Generated brand code: {brand_code}")
                else:
                    self.job_log.warning("Dropbox not configured, skipping brand code generation")

            except Exception as e:
                self.job_log.error(f"Brand code generation failed: {e}")
                # Don't fail - brand code is optional

    async def _run_distribution(self):
        """Run the distribution stage (YouTube, Dropbox, GDrive uploads)."""
        self.job_log.info("Starting distribution stage")
        self._update_progress(JobStatus.PACKAGING, 90, "Uploading files")

        # YouTube upload
        if self.config.enable_youtube_upload and self.config.youtube_credentials:
            await self._upload_to_youtube()

        # Dropbox upload
        if self.config.dropbox_path and self.config.brand_prefix:
            await self._upload_to_dropbox()

        # Google Drive upload
        if self.config.gdrive_folder_id:
            await self._upload_to_gdrive()

    async def _upload_to_youtube(self):
        """Upload video to YouTube."""
        self.job_log.info("Uploading to YouTube")

        # Find the best video file to upload (prefer MKV for FLAC audio, then lossless MP4)
        video_to_upload = None
        if self.result.final_video_mkv and os.path.isfile(self.result.final_video_mkv):
            video_to_upload = self.result.final_video_mkv
        elif self.result.final_video and os.path.isfile(self.result.final_video):
            video_to_upload = self.result.final_video
        elif self.result.final_video_lossy and os.path.isfile(self.result.final_video_lossy):
            video_to_upload = self.result.final_video_lossy

        if not video_to_upload:
            self.job_log.warning("No video file available for YouTube upload")
            return

        try:
            youtube_service = self._get_youtube_service()

            # Build video title
            title = f"{self.config.artist} - {self.config.title} (Karaoke)"

            # Build description
            description = self.config.youtube_description_template or ""
            if self.result.brand_code:
                description = f"{description}\n\nBrand Code: {self.result.brand_code}".strip()

            # Upload
            video_id, video_url = youtube_service.upload_video(
                video_path=video_to_upload,
                title=title,
                description=description,
                thumbnail_path=self.config.title_jpg_path,
                tags=["karaoke", self.config.artist, self.config.title],
                replace_existing=True,  # Server-side always replaces
            )

            if video_url:
                self.result.youtube_url = video_url
                self.job_log.info(f"Uploaded to YouTube: {video_url}")
            else:
                self.job_log.warning("YouTube upload did not return a URL")

        except Exception as e:
            self.job_log.error(f"YouTube upload failed: {e}")
            # Don't fail the pipeline - YouTube is optional

    async def _upload_to_dropbox(self):
        """Upload files to Dropbox."""
        self.job_log.info("Uploading to Dropbox")

        try:
            from backend.services.dropbox_service import get_dropbox_service

            dropbox = get_dropbox_service()
            if not dropbox.is_configured:
                self.job_log.warning("Dropbox not configured, skipping upload")
                return

            base_name = f"{self.config.artist} - {self.config.title}"
            folder_name = f"{self.result.brand_code or 'TRACK-0000'} - {base_name}"
            remote_folder = f"{self.config.dropbox_path}/{folder_name}"

            # Upload entire output directory
            dropbox.upload_folder(self.config.output_dir, remote_folder)

            # Create sharing link
            try:
                sharing_link = dropbox.create_shared_link(remote_folder)
                self.result.dropbox_link = sharing_link
                self.job_log.info(f"Dropbox sharing link: {sharing_link}")
            except Exception as e:
                self.job_log.warning(f"Failed to create Dropbox sharing link: {e}")

            self.job_log.info("Dropbox upload complete")

        except Exception as e:
            self.job_log.error(f"Dropbox upload failed: {e}")
            # Don't fail the pipeline - Dropbox is optional

    async def _upload_to_gdrive(self):
        """Upload files to Google Drive."""
        self.job_log.info("Uploading to Google Drive")

        try:
            from backend.services.gdrive_service import get_gdrive_service

            gdrive = get_gdrive_service()
            if not gdrive.is_configured:
                self.job_log.warning("Google Drive not configured, skipping upload")
                return

            base_name = f"{self.config.artist} - {self.config.title}"
            brand_code = self.result.brand_code or f"{self.config.brand_prefix or 'TRACK'}-0000"

            # Map result files to expected keys
            output_files = {
                'final_karaoke_lossy_mp4': self.result.final_video_lossy,
                'final_karaoke_lossy_720p_mp4': self.result.final_video_720p,
                'final_karaoke_cdg_zip': self.result.final_karaoke_cdg_zip,
            }

            uploaded = gdrive.upload_to_public_share(
                root_folder_id=self.config.gdrive_folder_id,
                brand_code=brand_code,
                base_name=base_name,
                output_files=output_files,
            )

            self.result.gdrive_files = uploaded
            self.job_log.info(f"Google Drive upload complete: {len(uploaded)} files")

        except Exception as e:
            self.job_log.error(f"Google Drive upload failed: {e}")
            # Don't fail the pipeline - GDrive is optional

    async def _run_notifications(self):
        """Run the notifications stage (Discord)."""
        self.job_log.info("Starting notifications stage")

        if not self.config.discord_webhook_url:
            self.job_log.debug("No Discord webhook configured, skipping notification")
            return

        if not self.result.youtube_url:
            self.job_log.info("No YouTube URL available, skipping Discord notification")
            return

        try:
            discord_service = self._get_discord_service()
            discord_service.post_video_notification(self.result.youtube_url)
            self.job_log.info("Discord notification sent")
        except Exception as e:
            self.job_log.error(f"Discord notification failed: {e}")
            # Don't fail the pipeline - notifications are optional


def create_orchestrator_config_from_job(
    job,
    temp_dir: str,
    youtube_credentials: Optional[Dict[str, Any]] = None,
    cdg_styles: Optional[Dict[str, Any]] = None,
) -> OrchestratorConfig:
    """
    Create an OrchestratorConfig from a job object.

    This is a helper function to bridge the existing job structure
    with the new orchestrator configuration.

    Args:
        job: Job object from Firestore
        temp_dir: Temporary directory with downloaded files
        youtube_credentials: Pre-loaded YouTube credentials
        cdg_styles: CDG style configuration

    Returns:
        OrchestratorConfig for the orchestrator
    """
    base_name = f"{job.artist} - {job.title}"

    # Determine instrumental file path
    instrumental_selection = job.state_data.get('instrumental_selection', 'clean')
    existing_instrumental = getattr(job, 'existing_instrumental_gcs_path', None)

    if existing_instrumental:
        ext = Path(existing_instrumental).suffix.lower()
        instrumental_path = os.path.join(temp_dir, f"{base_name} (Instrumental User){ext}")
    else:
        instrumental_suffix = "Clean" if instrumental_selection == 'clean' else "Backing"
        instrumental_path = os.path.join(temp_dir, f"{base_name} (Instrumental {instrumental_suffix}).flac")

    return OrchestratorConfig(
        job_id=job.job_id,
        artist=job.artist,
        title=job.title,

        # Input files
        title_video_path=os.path.join(temp_dir, f"{base_name} (Title).mov"),
        karaoke_video_path=os.path.join(temp_dir, f"{base_name} (With Vocals).mov"),
        instrumental_audio_path=instrumental_path,
        end_video_path=os.path.join(temp_dir, f"{base_name} (End).mov"),
        lrc_file_path=os.path.join(temp_dir, f"{base_name} (Karaoke).lrc"),
        title_jpg_path=os.path.join(temp_dir, f"{base_name} (Title).jpg"),

        # Output directory
        output_dir=temp_dir,

        # Feature flags
        enable_cdg=getattr(job, 'enable_cdg', False),
        enable_txt=getattr(job, 'enable_txt', False),
        enable_youtube_upload=getattr(job, 'enable_youtube_upload', False),

        # Service configurations
        brand_prefix=getattr(job, 'brand_prefix', None),
        discord_webhook_url=getattr(job, 'discord_webhook_url', None),
        youtube_credentials=youtube_credentials,
        youtube_description_template=getattr(job, 'youtube_description_template', None),
        cdg_styles=cdg_styles,

        # Dropbox/GDrive
        dropbox_path=getattr(job, 'dropbox_path', None),
        gdrive_folder_id=getattr(job, 'gdrive_folder_id', None),

        # Keep existing brand code
        keep_brand_code=getattr(job, 'keep_brand_code', None),

        # Encoding backend - auto selects GCE if available
        encoding_backend="auto",

        # Server-side defaults
        dry_run=False,
        non_interactive=True,
    )
