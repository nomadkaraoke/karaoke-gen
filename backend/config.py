"""
Configuration management for the karaoke generation backend.
"""
import os
import logging
from typing import Optional, Dict
from pydantic_settings import BaseSettings
from google.cloud import secretmanager


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings."""
    
    # Google Cloud
    google_cloud_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    gcs_bucket_name: str = os.getenv("GCS_BUCKET_NAME", "karaoke-gen-storage")
    gcs_temp_bucket: str = os.getenv("GCS_TEMP_BUCKET", "karaoke-gen-temp")
    gcs_output_bucket: str = os.getenv("GCS_OUTPUT_BUCKET", "karaoke-gen-outputs")
    firestore_collection: str = os.getenv("FIRESTORE_COLLECTION", "jobs")
    
    # Audio Separator API (for GPU processing)
    audio_separator_api_url: Optional[str] = os.getenv("AUDIO_SEPARATOR_API_URL")
    
    # External APIs (can be set via env or Secret Manager)
    audioshake_api_key: Optional[str] = os.getenv("AUDIOSHAKE_API_KEY")
    genius_api_key: Optional[str] = os.getenv("GENIUS_API_KEY")
    spotify_cookie: Optional[str] = os.getenv("SPOTIFY_COOKIE_SP_DC")
    rapidapi_key: Optional[str] = os.getenv("RAPIDAPI_KEY")
    
    # Authentication
    admin_tokens: Optional[str] = os.getenv("ADMIN_TOKENS")  # Comma-separated list
    
    # Application
    environment: str = os.getenv("ENVIRONMENT", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Processing
    max_concurrent_jobs: int = int(os.getenv("MAX_CONCURRENT_JOBS", "5"))
    job_timeout_seconds: int = int(os.getenv("JOB_TIMEOUT_SECONDS", "3600"))

    # Lyrics Correction Settings
    # Skip all auto-correction (both agentic AI and heuristic handlers).
    # When true, raw transcription goes directly to human review without any automatic fixes.
    # This is the default because auto-correction currently creates more work for reviewers.
    # Set SKIP_CORRECTION=false to re-enable auto-correction if quality improves.
    skip_correction: bool = os.getenv("SKIP_CORRECTION", "true").lower() in ("true", "1", "yes")

    # Agentic AI Correction (for lyrics correction via LLM)
    # Only used when skip_correction=false
    # When enabled, uses Gemini via Vertex AI for intelligent lyrics correction
    use_agentic_ai: bool = os.getenv("USE_AGENTIC_AI", "true").lower() in ("true", "1", "yes")
    agentic_ai_model: str = os.getenv("AGENTIC_AI_MODEL", "vertexai/gemini-3-flash-preview")
    # Timeout for agentic correction in seconds. If correction takes longer, abort and
    # use uncorrected transcription - human review will fix any issues.
    agentic_correction_timeout_seconds: int = int(os.getenv("AGENTIC_CORRECTION_TIMEOUT_SECONDS", "180"))

    # Cloud Tasks (for scalable worker coordination)
    # When enabled, workers are triggered via Cloud Tasks for guaranteed delivery
    # When disabled (default), workers are triggered via direct HTTP (for development)
    enable_cloud_tasks: bool = os.getenv("ENABLE_CLOUD_TASKS", "false").lower() in ("true", "1", "yes")
    gcp_region: str = os.getenv("GCP_REGION", "us-central1")
    
    # Cloud Run Jobs (for long-running video encoding)
    # When enabled AND enable_cloud_tasks is true, video worker uses Cloud Run Jobs
    # instead of Cloud Tasks. This supports encoding times >30 minutes (up to 24 hours).
    # Default is false - Cloud Tasks is sufficient for most videos (15-20 min).
    use_cloud_run_jobs_for_video: bool = os.getenv("USE_CLOUD_RUN_JOBS_FOR_VIDEO", "false").lower() in ("true", "1", "yes")

    # GCE Encoding Worker (for high-performance video encoding)
    # When enabled, video encoding is offloaded to a dedicated C4 GCE instance
    # with faster CPU (Intel Granite Rapids 3.9 GHz) instead of Cloud Run.
    # This provides 2-3x faster encoding times for CPU-bound FFmpeg libx264 encoding.
    use_gce_encoding: bool = os.getenv("USE_GCE_ENCODING", "false").lower() in ("true", "1", "yes")
    encoding_worker_url: Optional[str] = os.getenv("ENCODING_WORKER_URL")  # e.g., http://136.119.50.148:8080
    encoding_worker_api_key: Optional[str] = os.getenv("ENCODING_WORKER_API_KEY")

    # GCE Preview Encoding (for faster preview video generation)
    # When enabled, preview video encoding during lyrics review is offloaded to the GCE worker.
    # This reduces preview generation time from 60+ seconds to ~15-20 seconds.
    # Requires use_gce_encoding to be enabled and the GCE worker to support /encode-preview endpoint.
    use_gce_preview_encoding: bool = os.getenv("USE_GCE_PREVIEW_ENCODING", "false").lower() in ("true", "1", "yes")
    
    # Storage paths
    temp_dir: str = os.getenv("TEMP_DIR", "/tmp/karaoke-gen")

    # Worker logs storage mode
    # When enabled, worker logs are stored in a Firestore subcollection (jobs/{job_id}/logs)
    # instead of an embedded array. This avoids the 1MB document size limit.
    # Default is true for new deployments.
    use_log_subcollection: bool = os.getenv("USE_LOG_SUBCOLLECTION", "true").lower() in ("true", "1", "yes")
    
    # Flacfetch remote service (for torrent downloads)
    # When configured, audio search uses the remote flacfetch HTTP API instead of local flacfetch.
    # This is required for torrent downloads since Cloud Run doesn't support BitTorrent.
    flacfetch_api_url: Optional[str] = os.getenv("FLACFETCH_API_URL")  # e.g., http://10.0.0.5:8080
    flacfetch_api_key: Optional[str] = os.getenv("FLACFETCH_API_KEY")
    
    # Default distribution settings (can be overridden per-request)
    default_dropbox_path: Optional[str] = os.getenv("DEFAULT_DROPBOX_PATH")
    default_gdrive_folder_id: Optional[str] = os.getenv("DEFAULT_GDRIVE_FOLDER_ID")
    # Strip whitespace/newlines from webhook URL - common issue when env vars are set with trailing newlines
    default_discord_webhook_url: Optional[str] = (
        os.getenv("DEFAULT_DISCORD_WEBHOOK_URL", "").strip() or None
    )

    # Default values for web service jobs (YouTube/Dropbox distribution)
    default_enable_youtube_upload: bool = os.getenv("DEFAULT_ENABLE_YOUTUBE_UPLOAD", "false").lower() in ("true", "1", "yes")
    default_brand_prefix: Optional[str] = os.getenv("DEFAULT_BRAND_PREFIX")

    # Rate Limiting Configuration
    # Enable/disable rate limiting system-wide (useful for development)
    enable_rate_limiting: bool = os.getenv("ENABLE_RATE_LIMITING", "true").lower() in ("true", "1", "yes")
    # Maximum jobs a user can create per day (0 = unlimited)
    rate_limit_jobs_per_day: int = int(os.getenv("RATE_LIMIT_JOBS_PER_DAY", "5"))
    # Maximum YouTube uploads system-wide per day (0 = unlimited)
    rate_limit_youtube_uploads_per_day: int = int(os.getenv("RATE_LIMIT_YOUTUBE_UPLOADS_PER_DAY", "10"))
    # Maximum beta enrollments from same IP per day (0 = unlimited)
    rate_limit_beta_ip_per_day: int = int(os.getenv("RATE_LIMIT_BETA_IP_PER_DAY", "1"))

    # E2E test bypass key for rate limiting (set via secret in production)
    e2e_bypass_key: str = os.getenv("E2E_BYPASS_KEY", "")
    default_youtube_description: str = os.getenv(
        "DEFAULT_YOUTUBE_DESCRIPTION",
        "Karaoke video created with Nomad Karaoke (https://nomadkaraoke.com)\n\n"
        "AI-powered vocal separation and synchronized lyrics.\n\n"
        "#karaoke #music #singing #instrumental #lyrics"
    )

    # Default CDG/TXT generation settings
    # When True, CDG and TXT packages are generated by default (when a theme is set)
    # These can be overridden per-request via explicit enable_cdg/enable_txt parameters
    default_enable_cdg: bool = os.getenv("DEFAULT_ENABLE_CDG", "true").lower() in ("true", "1", "yes")
    default_enable_txt: bool = os.getenv("DEFAULT_ENABLE_TXT", "true").lower() in ("true", "1", "yes")

    # Push Notifications Configuration
    # When enabled, users can subscribe to push notifications for job status updates
    enable_push_notifications: bool = os.getenv("ENABLE_PUSH_NOTIFICATIONS", "false").lower() in ("true", "1", "yes")
    # Maximum number of push subscriptions per user (oldest removed when exceeded)
    max_push_subscriptions_per_user: int = int(os.getenv("MAX_PUSH_SUBSCRIPTIONS_PER_USER", "5"))
    # VAPID subject (email or URL for push service to contact)
    vapid_subject: str = os.getenv("VAPID_SUBJECT", "mailto:gen@nomadkaraoke.com")

    # Secret Manager cache
    _secret_cache: Dict[str, str] = {}
    
    class Config:
        env_file = ".env"
        case_sensitive = False
    
    def get_secret(self, secret_id: str) -> Optional[str]:
        """
        Get a secret from Google Secret Manager.
        
        Caches secrets in memory to avoid repeated API calls.
        Falls back to environment variables if Secret Manager unavailable.
        
        Args:
            secret_id: Secret name (e.g., "audioshake-api-key")
            
        Returns:
            Secret value or None if not found
        """
        # Check cache first
        if secret_id in self._secret_cache:
            return self._secret_cache[secret_id]
        
        # Check environment variable (development mode)
        env_var = secret_id.upper().replace('-', '_')
        env_value = os.getenv(env_var)
        if env_value:
            logger.debug(f"Using {secret_id} from environment variable")
            self._secret_cache[secret_id] = env_value
            return env_value
        
        # Try Secret Manager (production mode)
        if not self.google_cloud_project:
            logger.warning(f"Cannot fetch secret {secret_id}: No GCP project configured")
            return None
        
        try:
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{self.google_cloud_project}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            # Strip whitespace/newlines - common issue when secrets are created with trailing newlines
            secret_value = response.payload.data.decode('UTF-8').strip()
            
            # Cache it
            self._secret_cache[secret_id] = secret_value
            logger.info(f"Loaded secret {secret_id} from Secret Manager")
            return secret_value
            
        except Exception as e:
            logger.error(f"Failed to load secret {secret_id} from Secret Manager: {e}")
            return None


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings

