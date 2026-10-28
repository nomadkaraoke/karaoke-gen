"""
Configuration management for the karaoke generation backend.
"""
import os
import logging
from typing import Optional, Dict
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# google.cloud.secretmanager is imported lazily in get_secret() — importing it
# (and the google.api_core/grpc graph behind it) costs ~1.7s and config is
# imported by every backend entrypoint (cold-start work, 2026-09-22).

# Load .env for local development (no-op when absent, e.g. in Cloud Run).
# Must run before the Settings class body below, which reads os.getenv at
# class-definition time. Real environment variables take precedence.
load_dotenv()

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings."""
    
    # Google Cloud
    google_cloud_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    gcs_bucket_name: str = os.getenv("GCS_BUCKET_NAME", "karaoke-gen-storage")
    gcs_temp_bucket: str = os.getenv("GCS_TEMP_BUCKET", "karaoke-gen-temp")
    gcs_output_bucket: str = os.getenv("GCS_OUTPUT_BUCKET", "karaoke-gen-outputs")
    firestore_collection: str = os.getenv("FIRESTORE_COLLECTION", "jobs")

    # Nomad master fast-sync: push freshly-published Nomad 720p masters straight to the
    # Divebar GCS mirror so kjbox (which mirrors that folder every 5 min) picks them up
    # within minutes instead of waiting for the nightly Drive->GCS VM. Nomad-brand only.
    divebar_files_bucket: str = os.getenv("DIVEBAR_FILES_BUCKET", "nomadkaraoke-divebar-files")
    nomad_master_gcs_prefix: str = os.getenv("NOMAD_MASTER_GCS_PREFIX", "files/Nomad Karaoke/MP4-720p")
    nomad_master_fast_sync_enabled: bool = os.getenv("NOMAD_MASTER_FAST_SYNC_ENABLED", "true").lower() == "true"
    # Original-vocals guide: a padded isolated-vocals file (silence[intro] + vocals) pushed to
    # a sibling prefix in the same Divebar bucket, which kjbox mirrors into NOMAD-vocals-padded/
    # to power the "Original Vocals" sing-along slider. Same brand gating as the master push.
    nomad_vocals_guide_gcs_prefix: str = os.getenv("NOMAD_VOCALS_GUIDE_GCS_PREFIX", "files/Nomad Karaoke/vocals-padded")

    # KaraokeNerds community catalog: our own daily-refreshed copy of KN's community
    # (web-only, directly-playable) tracks, written by the authorized `kn-data-sync`
    # export job. The community-version check reads THIS, never scrapes karaokenerds.com.
    # Source of truth is BigQuery `karaoke_decide.karaokenerds_community`; the export job
    # also mirrors it to this gzipped-JSON GCS object, which the backend loads in-process.
    kn_community_bucket: str = os.getenv("KN_COMMUNITY_BUCKET", "nomadkaraoke-kn-data")
    kn_community_blob: str = os.getenv("KN_COMMUNITY_BLOB", "community/community-data-latest.json.gz")
    kn_community_ttl_seconds: int = int(os.getenv("KN_COMMUNITY_TTL_SECONDS", "21600"))  # 6h

    # Audio Separator API (for GPU processing)
    audio_separator_api_url: Optional[str] = os.getenv("AUDIO_SEPARATOR_API_URL")
    
    # External APIs (can be set via env or Secret Manager)
    audioshake_api_key: Optional[str] = os.getenv("AUDIOSHAKE_API_KEY")
    genius_api_key: Optional[str] = os.getenv("GENIUS_API_KEY")
    spotify_cookie: Optional[str] = os.getenv("SPOTIFY_COOKIE_SP_DC")
    rapidapi_key: Optional[str] = os.getenv("RAPIDAPI_KEY")
    
    # Authentication
    admin_tokens: Optional[str] = os.getenv("ADMIN_TOKENS")  # Comma-separated list
    scheduler_service_account: str = os.getenv(
        "SCHEDULER_SERVICE_ACCOUNT",
        "karaoke-backend@nomadkaraoke.iam.gserviceaccount.com"
    )
    
    # Application
    environment: str = os.getenv("ENVIRONMENT", "development")
    # Event-loop stall watchdog (backend/services/loop_watchdog.py): logs the
    # blocking stack when the single uvicorn loop stops ticking for >1s, and
    # records stalls >=5s to Firestore client_events (production only).
    loop_watchdog_enabled: bool = os.getenv("LOOP_WATCHDOG_ENABLED", "true").lower() in ("true", "1", "yes")
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
    # When enabled, uses Gemini (Developer API, key GEMINI_API_KEY) for intelligent lyrics correction
    use_agentic_ai: bool = os.getenv("USE_AGENTIC_AI", "true").lower() in ("true", "1", "yes")
    agentic_ai_model: str = os.getenv("AGENTIC_AI_MODEL", "gemini/gemini-3.8-flash")
    # Timeout for agentic correction in seconds. If correction takes longer, abort and
    # use uncorrected transcription - human review will fix any issues.
    agentic_correction_timeout_seconds: int = int(os.getenv("AGENTIC_CORRECTION_TIMEOUT_SECONDS", "180"))

    # Lyrics transcription outer (safety-net) timeout, scaled by audio duration.
    # Long inputs (e.g. 30+ minute mashups) legitimately take longer to transcribe,
    # so the timeout = clamp(floor, duration * per_audio_second, cap). The cap must
    # stay under the Cloud Run Job task timeout so we surface a clean RuntimeError
    # before the platform hard-kills the task. The lyrics-transcription-job sets the
    # task timeout to 3000s and overrides this cap to 2700s via env; the 1700s
    # default here is the safe value if that env is ever unset (under an 1800s task).
    transcription_timeout_floor_seconds: int = int(os.getenv("TRANSCRIPTION_TIMEOUT_FLOOR_SECONDS", "1200"))
    transcription_timeout_cap_seconds: int = int(os.getenv("TRANSCRIPTION_TIMEOUT_CAP_SECONDS", "1700"))
    transcription_timeout_per_audio_second: float = float(os.getenv("TRANSCRIPTION_TIMEOUT_PER_AUDIO_SECOND", "1.0"))

    # AI Credit Evaluation (anti-abuse gating for free credits)
    # When enabled, welcome and feedback credit grants are evaluated by Gemini
    # before granting. Fail-CLOSED: if evaluation fails, the decision becomes
    # "pending_review" (no auto-grant; an admin is notified to decide).
    credit_eval_enabled: bool = os.getenv("CREDIT_EVAL_ENABLED", "true").lower() in ("true", "1", "yes")
    credit_eval_model: str = os.getenv("CREDIT_EVAL_MODEL", "gemini-3.8-flash")

    # Custom Lyrics (LLM-powered) — used by the Custom Lyrics mode in the
    # Edit All Lyrics modal. Stateless service: takes operator-provided
    # custom-lyrics text/file and returns transformed lyric lines.
    custom_lyrics_model: str = os.getenv("CUSTOM_LYRICS_MODEL", "gemini-3.8-flash")
    custom_lyrics_max_file_mb: int = int(os.getenv("CUSTOM_LYRICS_MAX_FILE_MB", "5"))
    custom_lyrics_max_input_lines: int = int(os.getenv("CUSTOM_LYRICS_MAX_INPUT_LINES", "500"))
    custom_lyrics_max_iterations: int = int(os.getenv("CUSTOM_LYRICS_MAX_ITERATIONS", "4"))
    custom_lyrics_default_strictness: str = os.getenv("CUSTOM_LYRICS_DEFAULT_STRICTNESS", "balanced")
    custom_lyrics_max_output_lines_multiplier: float = float(os.getenv("CUSTOM_LYRICS_MAX_OUTPUT_LINES_MULTIPLIER", "2.0"))

    # Translated lyrics — per-job opt-in (Job.translation_language): the reviewed
    # lyrics are translated line-by-line just before the karaoke video renders.
    lyrics_translation_model: str = os.getenv("LYRICS_TRANSLATION_MODEL", "gemini-3.8-flash")

    # AI auto-correct suggestions — opt-in, user-triggered from the lyrics
    # review UI. Stateless: one whole-song LLM call returning word-level
    # suggestions the reviewer accepts/rejects individually.
    auto_correct_model: str = os.getenv("AUTO_CORRECT_MODEL", "gemini-3.8-flash")
    # Models used by the multi-model "compare" mode (semicolon-separated —
    # Cloud Run --set-env-vars is comma-delimited). Empty = single-model only.
    auto_correct_compare_models: str = os.getenv("AUTO_CORRECT_COMPARE_MODELS", "")
    # Proactively generate (and cache) auto-correct suggestions once
    # transcription + references are ready, so the review UI has them instantly.
    # This flag gates whether the lyrics worker fires the trigger; the actual
    # multi-model LLM call + caching runs on the API service (auto_correct_worker),
    # which holds the model credentials — the lyrics-transcription-job itself does
    # NOT need ANTHROPIC_API_KEY / AUTO_CORRECT_COMPARE_MODELS. Best-effort:
    # failures never block or fail the job. Default off.
    auto_correct_proactive_enabled: bool = os.getenv(
        "AUTO_CORRECT_PROACTIVE_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

    # Auto-approval ENFORCEMENT: when the auto-approval scorer is fully confident
    # (see backend/services/auto_approval/), skip the combined review screens and
    # go straight to render. Scoring + verdict recording happen on every job
    # regardless of this flag; it only gates actually skipping the human.
    # Per-job opt-out via Job.review_mode="always_review". Kill switch: false.
    auto_approval_enforce_enabled: bool = os.getenv(
        "AUTO_APPROVAL_ENFORCE_ENABLED", "true"
    ).lower() in ("true", "1", "yes")

    # Backing decider ENFORCEMENT (Phase 2B): allow a confident WITH_BACKING
    # verdict from the 3-stem decider to auto-select the with-backing
    # instrumental. Shadow-validated 2026-08-28/29 (20-job corpus + live
    # batteries + human review pass: 10/11 pick agreement, the one
    # auto-eligible job shipped identically to the human) -> default ON.
    # Default lives HERE, not per-environment env vars, because the executor
    # runs in BOTH the backend service and the audio-separation Cloud Run Job
    # (independent env; a service-only env flip left the audio_worker trigger
    # reading false). Kill switch: set false in ci.yml backend deploy env
    # AND infrastructure audio-separation-job envs (or revert this default).
    auto_approval_backing_keep_enabled: bool = os.getenv(
        "AUTO_APPROVAL_BACKING_KEEP_ENABLED", "true"
    ).lower() in ("true", "1", "yes")

    # Timing-plausibility gate: when the text signals say AUTO, check the word
    # timing against the lead-vocal stem (backend/services/auto_approval/
    # timing_check.py) and demote to review when it fires. Only ever ADDS
    # review (never auto-ships more), so it defaults on. Code-level default for
    # the same reason as backing_keep above (executor runs in both the backend
    # service and the audio-separation job). Kill switch: set false in ci.yml
    # backend deploy env AND the infrastructure audio-separation-job envs.
    auto_approval_timing_gate_enabled: bool = os.getenv(
        "AUTO_APPROVAL_TIMING_GATE_ENABLED", "true"
    ).lower() in ("true", "1", "yes")

    # Match judge — artist/title formatting + match-acceptance for the job
    # submission flow. Deterministic + catalog layers always run; the light AI
    # layer (Vertex Gemini Flash) fires only when those aren't confident.
    match_judge_enabled: bool = os.getenv("MATCH_JUDGE_ENABLED", "true").lower() in (
        "true", "1", "yes",
    )
    match_judge_model: str = os.getenv("MATCH_JUDGE_MODEL", "gemini-3.8-flash")
    # Bounds the AI call so a hung model never strands the submission UI. Kept
    # generous because Vertex returns 504 DEADLINE_EXCEEDED if generation can't
    # finish within this deadline (3s was too tight for gemini-3.5-flash); the
    # call still runs off the critical path and degrades to "none" on timeout.
    match_judge_timeout_ms: int = int(os.getenv("MATCH_JUDGE_TIMEOUT_MS", "12000"))

    # Batch karaoke-filename parser (kjbox download-naming). Reuses the Vertex
    # Gemini stack. Larger timeout than match_judge because it batches ~200 items.
    parse_titles_enabled: bool = os.getenv("PARSE_TITLES_ENABLED", "true").lower() in (
        "1", "true", "yes",
    )
    parse_titles_model: str = os.getenv("PARSE_TITLES_MODEL", "gemini-3.8-flash")
    parse_titles_timeout_ms: int = int(os.getenv("PARSE_TITLES_TIMEOUT_MS", "20000"))
    parse_titles_max_items: int = int(os.getenv("PARSE_TITLES_MAX_ITEMS", "200"))
    # Items per Gemini call: large client batches are split into concurrent
    # chunks of this size so one call never outgrows parse_titles_timeout_ms
    # (a 100-item single call did, blanking the whole batch).
    parse_titles_chunk_size: int = int(os.getenv("PARSE_TITLES_CHUNK_SIZE", "10"))

    # Cloud Tasks (for scalable worker coordination)
    # When enabled, workers are triggered via Cloud Tasks for guaranteed delivery
    # When disabled (default), workers are triggered via direct HTTP (for development)
    enable_cloud_tasks: bool = os.getenv("ENABLE_CLOUD_TASKS", "false").lower() in ("true", "1", "yes")
    gcp_region: str = os.getenv("GCP_REGION", "us-central1")
    # ALL Cloud Tasks queues live in us-central1, but workers running in other
    # regions (the GPU audio-separation-job sets GCP_REGION to its GPU region)
    # must still enqueue to the real queue location — building queue paths from
    # gcp_region 404s from those workers ("Queue does not exist").
    cloud_tasks_region: str = os.getenv("CLOUD_TASKS_REGION", "us-central1")
    # Region of the latency-critical CPU Cloud Run Jobs (audio-download-job,
    # lyrics-transcription-job, bulk-search-job, video-encoding-job). Cloud Run
    # Jobs in us-central1 queue 2-5 min before the container starts (measured
    # 2026-09-28 with a tiny sample image: us-central1 4-5 min vs us-east4 8-19s),
    # independent of image size or CPU. video-encoding-job reads ~0.3 GB/run from
    # the us-central1 bucket cross-region (~$1.60/mo, 2026-09-29 model). Rollback:
    # recreate the jobs in us-central1 via Pulumi, then set CPU_JOBS_REGION on the
    # service AND the jobs (see docs/TROUBLESHOOTING.md). Deliberately separate from gcp_region,
    # which these workers use for other resources (queues, encoding VM zones).
    cpu_jobs_region: str = os.getenv("CPU_JOBS_REGION", "us-east4")
    
    # Cloud Run Jobs (for long-running video encoding)
    # When enabled AND enable_cloud_tasks is true, video worker uses Cloud Run Jobs
    # instead of Cloud Tasks. This supports encoding times >30 minutes (up to 24 hours).
    # Default is false - Cloud Tasks is sufficient for most videos (15-20 min).
    # Inside a Cloud Run Job (Cloud Run sets CLOUD_RUN_JOB) both flags default to
    # true: a worker job that triggers the next stage (audio worker resuming a
    # parked encode, auto-approval starting a render) must never fall back to
    # Cloud Tasks -> a BackgroundTask on the API service. That runs the whole
    # video worker on the API's event loop (2026-10-19: 52s + 81s freezes,
    # ~30 requests each). Explicit env values still win.
    use_cloud_run_jobs_for_video: bool = os.getenv(
        "USE_CLOUD_RUN_JOBS_FOR_VIDEO", "true" if os.getenv("CLOUD_RUN_JOB") else "false"
    ).lower() in ("true", "1", "yes")

    # When enabled AND enable_cloud_tasks is true, the post-review render worker
    # also runs as a Cloud Run Job (reusing video-encoding-job with an args
    # override). As a BackgroundTask on the service it was killed by every
    # deploy rollout that landed mid-render — Cloud Run gives instances only
    # 10s after SIGTERM, far less than a render takes, so the job froze at
    # rendering_video until the 45-min stuck-job sweep (incident 2026-09-13,
    # job 41e06b90; same class as incident 2026-03-08 for the video worker).
    use_cloud_run_jobs_for_render: bool = os.getenv(
        "USE_CLOUD_RUN_JOBS_FOR_RENDER", "true" if os.getenv("CLOUD_RUN_JOB") else "false"
    ).lower() in ("true", "1", "yes")

    # GCE Encoding Worker (for high-performance video encoding)
    # When enabled, video encoding is offloaded to a dedicated C4 GCE instance
    # with faster CPU (Intel Granite Rapids 3.9 GHz) instead of Cloud Run.
    # This provides 2-3x faster encoding times for CPU-bound FFmpeg libx264 encoding.
    use_gce_encoding: bool = os.getenv("USE_GCE_ENCODING", "false").lower() in ("true", "1", "yes")
    encoding_worker_url: Optional[str] = os.getenv("ENCODING_WORKER_URL")  # e.g., http://136.119.50.148:8080
    encoding_worker_api_key: Optional[str] = os.getenv("ENCODING_WORKER_API_KEY")

    # Optional capacity-fallback VMs in alternate zones. JSON list of objects:
    #   '[{"vm":"encoding-worker-fallback-a","zone":"us-central1-a","ip":"34.x.x.x"}]'
    # Empty by default. After `pulumi up` provisions the fallback VMs (Phase 3
    # of the capacity-resilience plan), populate this var with their public
    # IPs to enable multi-zone failover when us-central1-c is exhausted.
    encoding_worker_fallback_vms: Optional[str] = os.getenv("ENCODING_WORKER_FALLBACK_VMS")

    # GCE Preview Encoding (for faster preview video generation)
    # When enabled, preview video encoding during lyrics review is offloaded to the GCE worker.
    # This reduces preview generation time from 60+ seconds to ~15-20 seconds.
    # Requires use_gce_encoding to be enabled and the GCE worker to support /encode-preview endpoint.
    use_gce_preview_encoding: bool = os.getenv("USE_GCE_PREVIEW_ENCODING", "false").lower() in ("true", "1", "yes")
    
    # Karaoke-Decide main API: catalog autocomplete, and the partner call that
    # erases a deleted account's decide data (DELETE /api/partner/gen/users)
    karaoke_decide_api_url: str = os.getenv("KARAOKE_DECIDE_API_URL", "https://decide.nomadkaraoke.com")

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

    # Flacfetch client retry settings
    # Retries help handle transient network failures when calling the flacfetch service
    flacfetch_retry_max_attempts: int = int(os.getenv("FLACFETCH_RETRY_MAX_ATTEMPTS", "9"))  # Total attempts (1 initial + 8 retries)
    flacfetch_retry_min_wait: float = float(os.getenv("FLACFETCH_RETRY_MIN_WAIT", "10.0"))  # seconds
    flacfetch_retry_max_wait: float = float(os.getenv("FLACFETCH_RETRY_MAX_WAIT", "60.0"))  # seconds

    # Search retry settings are much lighter than the download defaults above:
    # search is called synchronously from user-facing requests that sit behind
    # Cloudflare's ~100s edge timeout, so there's only room for one quick retry
    # (worst case: 60s timeout + 1s wait + fast cache-hit retry).
    flacfetch_search_retry_max_attempts: int = int(os.getenv("FLACFETCH_SEARCH_RETRY_MAX_ATTEMPTS", "2"))
    flacfetch_search_retry_wait: float = float(os.getenv("FLACFETCH_SEARCH_RETRY_WAIT", "1.0"))  # seconds
    # Total budget across all search attempts, kept under Cloudflare's ~100s edge
    # deadline so callers get a clean FlacfetchServiceError instead of an edge 524.
    flacfetch_search_total_timeout: float = float(os.getenv("FLACFETCH_SEARCH_TOTAL_TIMEOUT", "95.0"))  # seconds

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

    # Requests voting board — Phase 2 daily free-community-track automation.
    # Master kill-switch: when False the daily picker claims the day and records a
    # "skipped" lock but never grants credits or creates a job (safe to deploy dark).
    community_daily_pick_enabled: bool = os.getenv(
        "COMMUNITY_DAILY_PICK_ENABLED", "false"
    ).lower() in ("true", "1", "yes")
    # Max distinct voters the 24h ownership handoff will try before parking a track.
    community_handoff_max_attempts: int = int(os.getenv("COMMUNITY_HANDOFF_MAX_ATTEMPTS", "5"))
    # Hours an owner has to complete their review before the job is handed off.
    community_handoff_hours: int = int(os.getenv("COMMUNITY_HANDOFF_HOURS", "24"))
    # When a flagged pick is "kept on board", how long before it can be re-flagged.
    community_review_snooze_days: int = int(os.getenv("COMMUNITY_REVIEW_SNOOZE_DAYS", "30"))

    # KaraokeHunt retired-app request interceptor (POST /api/karaokehunt/request).
    # Shared secret set by the Cloudflare Worker on create.karaokehunt.com; the
    # endpoint 503s while this is empty, so deploys are dark until the secret is
    # wired (Secret Manager: karaokehunt-forwarder-secret).
    karaokehunt_forwarder_secret: str = os.getenv("KARAOKEHUNT_FORWARDER_SECRET", "")
    # Max conversion jobs per UTC day (bounds unattended generation spend);
    # over-cap requests fall through to the free community requests board.
    karaokehunt_daily_job_cap: int = int(os.getenv("KARAOKEHUNT_DAILY_JOB_CAP", "3"))

    # kjbox partner API (/api/kjbox/*): singer email-code sign-in + show credits
    # for the karaoke-night "make it" flow. Every endpoint 503s while the secret is
    # empty, so deploys are dark until it's wired (Secret Manager: kjbox-partner-secret).
    kjbox_partner_secret: str = os.getenv("KJBOX_PARTNER_SECRET", "")
    # karaoke-decide's shared secret (header X-Partner-Secret, Secret Manager:
    # decide-gen-partner-secret): decide sends it to POST /api/kjbox/catalog/resolve, and
    # gen sends it to decide's DELETE /api/partner/gen/users on account deletion.
    # Empty = decide is not accepted, and account deletion 503s at the decide step.
    decide_partner_secret: str = os.getenv("DECIDE_GEN_PARTNER_SECRET", "")
    # Partner-wide cap on NEW gen accounts created via kjbox per rolling 24h (a venue
    # shares one IP, so gen's per-IP signup cap is bypassed for kjbox; this bounds it).
    kjbox_signup_cap_per_24h: int = int(os.getenv("KJBOX_SIGNUP_CAP_PER_24H", "100"))
    # Max emailed sign-in codes per address per rolling hour.
    kjbox_codes_per_email_per_hour: int = int(os.getenv("KJBOX_CODES_PER_EMAIL_PER_HOUR", "5"))
    # Max "free at the show" credits kjbox may grant one user per rolling 24h.
    kjbox_show_credits_per_user_per_24h: int = int(os.getenv("KJBOX_SHOW_CREDITS_PER_USER_PER_24H", "5"))

    # gen email-code sign-in (/api/users/auth/send-code + verify-code).
    # HMAC pepper for stored codes; when empty it's derived from the first admin
    # token (see backend/api/routes/login_code.py:_resolve_pepper).
    login_code_pepper: str = os.getenv("LOGIN_CODE_PEPPER", "")
    # Max emailed sign-in codes per address / per client IP per rolling hour.
    login_codes_per_email_per_hour: int = int(os.getenv("LOGIN_CODES_PER_EMAIL_PER_HOUR", "5"))
    login_codes_per_ip_per_hour: int = int(os.getenv("LOGIN_CODES_PER_IP_PER_HOUR", "20"))

    # Private (non-published) track distribution settings
    default_private_dropbox_path: Optional[str] = os.getenv("DEFAULT_PRIVATE_DROPBOX_PATH")
    default_private_brand_prefix: Optional[str] = os.getenv("DEFAULT_PRIVATE_BRAND_PREFIX", "NOMADNP")

    # Output files NOT uploaded to Nomad's own Dropbox folders (DEFAULT_DROPBOX_PATH /
    # DEFAULT_PRIVATE_DROPBOX_PATH), as "|"-separated filename suffixes. They stay in
    # GCS (job-page downloads, YouTube upload, AWS backup) — this only saves the
    # multi-GB internet egress + upload time. Tenant Dropbox folders are exempt
    # (B2B tenants expect the full set). Default skips only the lossless 4K MP4,
    # which no customer-facing text promises in Dropbox. The lossless MKV IS promised
    # there (completion email + the Fiverr bot), so add
    # " (Final Karaoke Lossless 4k).mkv" only after updating those.
    # See docs/archive/2026-09-26-encoding-cost-cuts.md.
    dropbox_skip_output_suffixes: str = os.getenv(
        "DROPBOX_SKIP_OUTPUT_SUFFIXES", " (Final Karaoke Lossless 4k).mp4"
    )

    # YouTube Data API v3 Quota Configuration
    # Daily quota limit (YouTube grants 10,000 units/day by default)
    youtube_quota_daily_limit: int = int(os.getenv("YOUTUBE_QUOTA_DAILY_LIMIT", "10000"))
    # Estimated units consumed per upload (videos.insert=100 + search.list=100 + thumbnails.set=50 + buffer)
    youtube_quota_upload_cost: int = int(os.getenv("YOUTUBE_QUOTA_UPLOAD_COST", "300"))
    # Safety margin to reserve (avoid hitting exact limit)
    youtube_quota_safety_margin: int = int(os.getenv("YOUTUBE_QUOTA_SAFETY_MARGIN", "500"))
    # E2E test bypass key for rate limiting (set via secret in production)
    e2e_bypass_key: str = os.getenv("E2E_BYPASS_KEY", "")
    # Local-stack E2E mode (scripts/local-stack.sh): workers are replaced by in-process
    # fakes that walk jobs through their real states with canned media, the
    # /api/test/* router is mounted, and external lookups (Secret Manager, DeBounce,
    # ip-api, encoding VM) are skipped. Startup refuses it in production.
    # See backend/services/fake_workers/ and docs/TESTING.md § Local stack.
    fake_workers: bool = os.getenv("FAKE_WORKERS", "").lower() in ("1", "true", "yes")
    # Mobile app account deletion (DELETE /api/users/me). Off in prod until
    # Andrew's manual pass; on in the local stack.
    account_deletion_enabled: bool = os.getenv("ACCOUNT_DELETION_ENABLED", "").lower() in ("1", "true", "yes")
    # App Review demo login: inert unless this AND the app-review-demo-code secret are set.
    app_review_demo_email: str = os.getenv("APP_REVIEW_DEMO_EMAIL", "")
    # Secret Manager: app-review-demo-code, mounted as APP_REVIEW_DEMO_CODE.
    app_review_demo_code: str = os.getenv("APP_REVIEW_DEMO_CODE", "")
    # RevenueCat (mobile in-app credit packs). The webhook (POST
    # /api/users/webhooks/revenuecat) compares the Authorization header to this
    # value and 503s while it is empty (Secret Manager: revenuecat-webhook-auth).
    revenuecat_webhook_auth: str = os.getenv("REVENUECAT_WEBHOOK_AUTH", "")
    # RevenueCat secret REST API key (Secret Manager: revenuecat-secret-api-key).
    revenuecat_secret_api_key: str = os.getenv("REVENUECAT_SECRET_API_KEY", "")
    # RevenueCat project the v2 key belongs to (account deletion's customer delete).
    revenuecat_project_id: str = os.getenv("REVENUECAT_PROJECT_ID", "9ea947ce")
    # URL scheme of the Nomad Karaoke mobile app. App checkouts return through
    # GET /api/users/credits/app-return, which 302s to {scheme}://checkout?status=…
    mobile_app_scheme: str = os.getenv("MOBILE_APP_SCHEME", "nomadkaraoke")
    # Accepted `aud` values for Google ID tokens (POST /api/users/auth/google),
    # comma-separated. OAuth client IDs are public, not secrets. Defaults: the
    # "Nomad Karaoke Web" client (the audience of tokens from Android Credential
    # Manager / iOS with serverClientId) and the iOS client (iOS SDK tokens can
    # carry it). The Android client is device-only and never an audience. See
    # docs/ARCHITECTURE.md § Mobile app sign-in. Empty → the endpoint returns 503.
    google_oauth_client_ids: str = os.getenv(
        "GOOGLE_OAUTH_CLIENT_IDS",
        "718638054799-fush0smd1e4icq9pt0immp8n59jjlebh.apps.googleusercontent.com,"
        "718638054799-hre535n83p8i85tsqf6ai759nlubhd2o.apps.googleusercontent.com",
    )
    # Accepted `aud` values for Apple ID tokens (POST /api/users/auth/apple):
    # the app's bundle ID (native iOS), plus any Services ID used for web/Android:
    # com.nomadkaraoke.sing = Sign in with Apple JS on the kjbox singer page
    # (sing.nomadkaraoke.com → POST /api/kjbox/auth/apple).
    apple_signin_audiences: str = os.getenv("APPLE_SIGNIN_AUDIENCES", "com.nomadkaraoke.mobile,com.nomadkaraoke.sing")

    # Sign in with Apple token revocation (account deletion). The .p8 PEM text
    # and key id come from Secret Manager; unset => revocation is skipped.
    apple_siwa_key_p8: Optional[str] = os.getenv("APPLE_SIWA_KEY_P8")
    apple_siwa_key_id: Optional[str] = os.getenv("APPLE_SIWA_KEY_ID")
    apple_team_id: str = os.getenv("APPLE_TEAM_ID", "55B76HN73D")
    apple_client_id: str = os.getenv("APPLE_CLIENT_ID", "com.nomadkaraoke.mobile")
    # Canonical YouTube description template. Placeholders ({title}, {artist},
    # {artist_hashtag}, {brand_code}) are filled by
    # backend.services.youtube_description.render_youtube_description, which is the
    # single source of truth shared with the bulk-rewrite tool
    # (scripts/youtube-descriptions). Keep those two in sync when editing this.
    default_youtube_description: str = os.getenv(
        "DEFAULT_YOUTUBE_DESCRIPTION",
        '🎤 Karaoke version of "{title}" by {artist}: original audio, lead vocals '
        "removed, hand-synced lyrics.\n"
        "▶️ Make a karaoke video of ANY song: "
        "https://nomadkaraoke.com/r/youtube?utm_source=youtube&utm_medium=description&utm_campaign=make_cta_top\n\n"
        "⬆️ REQUEST THE NEXT NOMAD RELEASE\n"
        "Vote for the songs you want us to make next — the most-upvoted requests get "
        "made into karaoke videos and published free, right here on this channel. "
        "Nothing to buy, no password needed:\n"
        "👉 https://requests.nomadkaraoke.com/?utm_source=youtube&utm_medium=description&utm_campaign=requests\n\n"
        "▶️ MAKE A VIDEO LIKE THIS FOR ANY SONG\n"
        "Create studio-quality karaoke videos of ANY song, ready in under an hour:\n"
        "👉 https://nomadkaraoke.com/r/youtube?utm_source=youtube&utm_medium=description&utm_campaign=make_cta\n"
        "Follow that link for 50% off all credit purchases for your first 30 days.\n\n"
        "🎶 KARAOKE LOVER LINKS\n"
        "• Nomad Karaoke Community Discord: https://discord.nomadkaraoke.com\n"
        "• Decide what to sing at karaoke: https://decide.nomadkaraoke.com\n"
        "• Search all community karaoke tracks: https://karaokenerds.com\n\n"
        "🔔 Subscribe for new karaoke tracks, and comment to show some love!\n\n"
        "#karaoke #{artist_hashtag} #instrumental #lyrics #singalong\n"
        "—\n"
        "COPYRIGHT: I don't own the rights to the original music; all rights belong "
        "to the respective copyright holders. If you enjoy the song, please buy the "
        "original and support the artist. Under §107 of the U.S. Copyright Act 1976, "
        'allowance is made for "fair use" (criticism, comment, teaching, and '
        "research).\n\n"
        "Brand Code: {brand_code}"
    )

    # Offer an opt-in "tip the creator" optional item on gen Stripe Checkout pages
    # (credits + Made For You). Kill switch: CHECKOUT_TIP_ENABLED=false.
    checkout_tip_enabled: bool = os.getenv("CHECKOUT_TIP_ENABLED", "true").lower() in ("true", "1", "yes")
    # Free-to-paid conversion emails (backend/services/conversion_emails.py).
    # OFF until Andrew approves the copy: CONVERSION_EMAILS_ENABLED=true sends E1
    # ("your next song" + time-limited discount) and E2 (offer ending) on Postmark's
    # broadcast stream; REVIEW_LAST_CHANCE_ENABLED=true adds the 42h review nudge.
    conversion_emails_enabled: bool = os.getenv("CONVERSION_EMAILS_ENABLED", "false").lower() in ("true", "1", "yes")
    review_last_chance_enabled: bool = os.getenv("REVIEW_LAST_CHANCE_ENABLED", "false").lower() in ("true", "1", "yes")
    conversion_offer_percent: int = int(os.getenv("CONVERSION_OFFER_PERCENT", "25"))
    conversion_offer_hours: int = int(os.getenv("CONVERSION_OFFER_HOURS", "48"))
    conversion_e1_delay_hours: float = float(os.getenv("CONVERSION_E1_DELAY_HOURS", "2"))
    conversion_e2_hours_before_expiry: float = float(os.getenv("CONVERSION_E2_HOURS_BEFORE_EXPIRY", "8"))
    # CAN-SPAM: commercial email must carry a physical postal address.
    # Default = the business address already published on nomadkaraoke.com.
    commercial_email_postal_address: str = os.getenv(
        "COMMERCIAL_EMAIL_POSTAL_ADDRESS", "Nomad Karaoke, 612 Joan St, Columbia, SC 29203, USA"
    )

    # YouTube description back-catalogue rewrite (scheduled daily drain worker).
    # Rewrites existing channel videos to match `default_youtube_description`.
    # Reusable: whenever the template changes, the worker re-drains the channel.
    youtube_backfill_enabled: bool = os.getenv("YOUTUBE_BACKFILL_ENABLED", "true").lower() in ("true", "1", "yes")
    # Hard ceiling on updates per daily run (each costs ~50 quota units).
    youtube_backfill_daily_max_updates: int = int(os.getenv("YOUTUBE_BACKFILL_DAILY_MAX_UPDATES", "150"))
    # Quota units to leave untouched for the live upload pipeline each day.
    youtube_backfill_quota_reserve: int = int(os.getenv("YOUTUBE_BACKFILL_QUOTA_RESERVE", "3000"))
    # Refresh tags too (not just description) when rewriting.
    youtube_backfill_enrich_tags: bool = os.getenv("YOUTUBE_BACKFILL_ENRICH_TAGS", "true").lower() in ("true", "1", "yes")
    # Where the daily progress + completion emails are sent.
    youtube_backfill_report_email: str = os.getenv("YOUTUBE_BACKFILL_REPORT_EMAIL", "andrew@nomadkaraoke.com")

    # YouTube comment auto-reply (backend/workers/youtube_comment_bot_worker.py):
    # replies to song requests in channel comments with a requests-board link.
    # OFF by default; go-live only after Andrew signs off a local replay
    # (scripts/youtube-comments/replay.py). Dry runs work while off.
    youtube_comment_autoreply_enabled: bool = os.getenv("YOUTUBE_COMMENT_AUTOREPLY_ENABLED", "false").lower() in ("true", "1", "yes")
    youtube_comment_autoreply_max_replies: int = int(os.getenv("YOUTUBE_COMMENT_AUTOREPLY_MAX_REPLIES", "20"))
    youtube_comment_autoreply_min_confidence: float = float(os.getenv("YOUTUBE_COMMENT_AUTOREPLY_MIN_CONFIDENCE", "0.8"))
    # ISO date/time: on the FIRST run, comments since this are eligible (a one-time
    # backfill of requests nobody answered). Empty = only comments after go-live.
    youtube_comment_autoreply_start: str = os.getenv("YOUTUBE_COMMENT_AUTOREPLY_START", "")

    # Storage retention (backend/services/storage_retention.py): a daily job purges
    # regenerable files (big finals, rendered videos, stems, ...) from jobs
    # completed more than `storage_retention_min_age_days` ago. DRY-RUN BY DEFAULT:
    # it only writes a report until STORAGE_RETENTION_DRY_RUN=false is set after a
    # human has reviewed one.
    storage_retention_enabled: bool = os.getenv("STORAGE_RETENTION_ENABLED", "true").lower() in ("true", "1", "yes")
    storage_retention_dry_run: bool = os.getenv("STORAGE_RETENTION_DRY_RUN", "true").lower() not in ("false", "0", "no")
    storage_retention_min_age_days: int = int(os.getenv("STORAGE_RETENTION_MIN_AGE_DAYS", "30"))
    # Real (non-dry) runs purge at most this many jobs per run, resuming from a cursor.
    storage_retention_max_jobs_per_run: int = int(os.getenv("STORAGE_RETENTION_MAX_JOBS_PER_RUN", "50"))
    # Comma-separated tenant ids whose jobs are never purged.
    storage_retention_excluded_tenants: str = os.getenv("STORAGE_RETENTION_EXCLUDED_TENANTS", "")

    # Customer "Regenerate video" limits (per job / per user, rolling 24h).
    regenerate_max_per_job_per_day: int = int(os.getenv("REGENERATE_MAX_PER_JOB_PER_DAY", "3"))
    regenerate_max_per_user_per_day: int = int(os.getenv("REGENERATE_MAX_PER_USER_PER_DAY", "10"))

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
        
        # Local stack runs offline on emulators: never reach for Secret Manager.
        if self.fake_workers is True and not is_production():
            return None

        # Try Secret Manager (production mode)
        if not self.google_cloud_project:
            logger.warning(f"Cannot fetch secret {secret_id}: No GCP project configured")
            return None
        
        try:
            from google.cloud import secretmanager

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


def is_production() -> bool:
    """True when running in a deployed/production environment.

    Cloud Run automatically sets ``K_SERVICE``, so we treat its presence as
    production even if ``ENVIRONMENT`` were somehow unset — a production-safe
    default. Local dev/test have no ``K_SERVICE`` and a non-"production"
    ``ENVIRONMENT``. (Fallback audit 2026-06-09, Theme 7.)
    """
    env = (os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "").lower()
    if env == "production":
        return True
    return bool(os.environ.get("K_SERVICE"))


# Env vars that MUST be explicitly set in production. A missing one used to
# silently fall back to a dev default and produce wrong behaviour with only a
# warning (wrong GCP project for secret lookups, wrong GCS bucket, localhost
# worker URL). We only require vars that infra confirms are set on every service
# (GCS temp/output buckets are intentionally excluded — their defaults are
# load-bearing and not set via env). (Fallback audit 2026-06-09, Theme 7.)
_REQUIRED_PRODUCTION_ENV_VARS = (
    "GOOGLE_CLOUD_PROJECT",
    "GCS_BUCKET_NAME",
    "CLOUD_RUN_SERVICE_URL",
)


def validate_production_config() -> None:
    """Fail fast at startup if required production env vars are missing.

    In production each var in ``_REQUIRED_PRODUCTION_ENV_VARS`` must be explicitly
    set; otherwise the service would silently run with a dev default (wrong GCP
    project / wrong GCS bucket / localhost worker URL) and appear to work while
    doing the wrong thing. Raises RuntimeError listing every missing var. No-op
    outside production. (Fallback audit 2026-06-09, Theme 7.)
    """
    if not is_production():
        return
    if settings.fake_workers is True:
        raise RuntimeError(
            "FAKE_WORKERS is set in production. It replaces every worker with a fake "
            "and mounts the unauthenticated /api/test/* router. Refusing to start."
        )
    missing = [v for v in _REQUIRED_PRODUCTION_ENV_VARS if not (os.environ.get(v) or "").strip()]
    if missing:
        raise RuntimeError(
            "Production misconfiguration: required environment variable(s) not set: "
            + ", ".join(missing)
            + ". Refusing to start with silent dev defaults (wrong GCP project / "
            "GCS bucket / localhost worker URL)."
        )

