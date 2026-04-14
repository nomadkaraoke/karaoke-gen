"""Configuration for the production error monitoring service.

All constants, service lists, and environment-driven configuration
functions used across the error monitor modules.
"""

import os

# ---------------------------------------------------------------------------
# GCP project settings
# ---------------------------------------------------------------------------

GCP_PROJECT: str = os.environ.get("GCP_PROJECT", "nomadkaraoke")
GCP_REGION: str = os.environ.get("GCP_REGION", "us-central1")

# ---------------------------------------------------------------------------
# Log querying settings
# ---------------------------------------------------------------------------

#: How far back to look for errors on each run (minutes).
LOOKBACK_MINUTES: int = 15

#: Maximum number of log entries to fetch per service per run.
MAX_LOG_ENTRIES: int = 500

# ---------------------------------------------------------------------------
# Monitored services
# ---------------------------------------------------------------------------

MONITORED_CLOUD_RUN_SERVICES: list[str] = [
    "karaoke-backend",
    "karaoke-decide",
    "audio-separator",
    # Gen2 Cloud Functions appear as Cloud Run services in logs
    "gdrive-validator",
    "github-runner-manager",
    "backup-to-aws",
    "divebar-mirror",
    "kn-data-sync",
    "divebar-lookup",
    "encoding-worker-idle-shutdown",
]

MONITORED_CLOUD_RUN_JOBS: list[str] = [
    "video-encoding-job",
    "lyrics-transcription-job",
    "audio-separation-job",
    "audio-download-job",
]

# Gen2 Cloud Functions log as cloud_run_revision, not cloud_function.
# Their names are in MONITORED_CLOUD_RUN_SERVICES above.
MONITORED_CLOUD_FUNCTIONS: list[str] = []

MONITORED_GCE_INSTANCES: list[str] = [
    "encoding-worker-a",
    "encoding-worker-b",
    "flacfetch-vm",
    "divebar-sync-vm",
]

# ---------------------------------------------------------------------------
# Service dependency map
# Human-readable description of which services depend on which workers.
# Used as LLM context when generating root-cause analysis.
# ---------------------------------------------------------------------------

SERVICE_DEPENDENCY_MAP: str = """\
karaoke-backend (main API)
  → video-encoding-job        (Cloud Run Job, triggered per order)
  → lyrics-transcription-job  (Cloud Run Job, triggered after audio-sep)
  → audio-separation-job      (Cloud Run Job, runs on Cloud Run GPU)
  → audio-download-job        (Cloud Run Job, uses flacfetch-vm)
  → gdrive-validator          (Cloud Function, validates output files)
  → encoding-worker-a/b       (GCE VMs, long-running encoding workers)

karaoke-decide (catalog / discovery API)
  → kn_data_sync              (Cloud Function, syncs KaraokeNerds catalog)
  → divebar_mirror            (Cloud Function, mirrors Divebar catalog)
  → divebar_lookup            (Cloud Function, single-song lookup)

audio-separator (Cloud Run service, GPU stem separation)
  → audio-separation-job      (Cloud Run Job, launched by audio-separator)

flacfetch-vm (GCE VM)
  → audio-download-job        (initiated by karaoke-backend)

divebar-sync-vm (GCE VM)
  → divebar_mirror            (Cloud Function, reads from this VM)

Shared infrastructure:
  → runner_manager            (Cloud Function, manages encoding worker lifecycle)
  → encoding_worker_idle      (Cloud Function, shuts down idle encoding workers)
  → backup_to_aws             (Cloud Function, daily GCS → S3 backup)
"""

# ---------------------------------------------------------------------------
# LLM analysis settings
# ---------------------------------------------------------------------------

LLM_ANALYSIS_MODEL: str = os.environ.get("LLM_ANALYSIS_MODEL", "gemini-2.0-flash-001")
LLM_VERTEX_LOCATION: str = os.environ.get("LLM_VERTEX_LOCATION", "us-central1")

#: Minimum number of active error patterns required before triggering LLM analysis.
MIN_PATTERNS_FOR_ANALYSIS: int = 3

# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------

#: An error count is a spike if it exceeds SPIKE_MULTIPLIER × baseline average.
SPIKE_MULTIPLIER: float = 5.0

#: Minimum absolute error count before spike detection fires (avoids noise).
SPIKE_MIN_COUNT: int = 5

# ---------------------------------------------------------------------------
# Auto-resolution settings
# ---------------------------------------------------------------------------

#: Multiply LOOKBACK_MINUTES by this to get auto-resolve window in minutes.
AUTO_RESOLVE_MULTIPLIER: int = 8

#: Minimum hours before a pattern can be auto-resolved.
AUTO_RESOLVE_MIN_HOURS: int = 6

#: Maximum hours before a pattern is force-resolved even if still active.
AUTO_RESOLVE_MAX_HOURS: int = 168  # 1 week

#: Fallback resolve window (hours) if baseline data is unavailable.
AUTO_RESOLVE_FALLBACK_HOURS: int = 48

# ---------------------------------------------------------------------------
# Discord settings
# ---------------------------------------------------------------------------

#: Maximum Discord messages to send per monitor run (rate-limit guard).
MAX_DISCORD_MESSAGES_PER_RUN: int = 10

#: Discord API hard limit on message length.
DISCORD_MAX_MESSAGE_LENGTH: int = 2000

# ---------------------------------------------------------------------------
# Pattern tracking settings
# ---------------------------------------------------------------------------

#: Maximum number of active error patterns to track in Firestore.
MAX_ACTIVE_PATTERNS: int = 500

#: Rolling window used when computing error baselines.
ROLLING_WINDOW_DAYS: int = 7

#: Maximum length of a normalized error message stored in Firestore.
MAX_NORMALIZED_MESSAGE_LENGTH: int = 200

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_llm_enabled() -> bool:
    """Return True if LLM analysis is enabled via the LLM_ANALYSIS_ENABLED env var."""
    return os.environ.get("LLM_ANALYSIS_ENABLED", "false").strip().lower() == "true"


def get_discord_webhook_secret_name() -> str:
    """Return the Secret Manager secret name for the Discord webhook URL."""
    return os.environ.get("DISCORD_WEBHOOK_SECRET_NAME", "error-monitor-discord-webhook")


def get_digest_mode() -> bool:
    """Return True if the monitor should run in digest mode (batch alerts instead of immediate).

    In digest mode all alerts are bundled into a single Discord message sent at
    the end of the run, rather than sending one message per error pattern.
    """
    return os.environ.get("DIGEST_MODE", "false").strip().lower() == "true"
