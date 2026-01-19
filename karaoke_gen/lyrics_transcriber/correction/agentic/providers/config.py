from dataclasses import dataclass
from typing import Optional
import os


@dataclass(frozen=True)
class ProviderConfig:
    """Centralized configuration for AI providers.

    Values are loaded from environment variables to keep credentials out of code.
    This module is safe to import during setup; it does not perform any network I/O.
    """

    openai_api_key: Optional[str]
    anthropic_api_key: Optional[str]
    google_api_key: Optional[str]
    openrouter_api_key: Optional[str]
    privacy_mode: bool
    cache_dir: str

    # GCP/Vertex AI settings
    # Note: Gemini 3 models require 'global' location (not regional like us-central1)
    gcp_project_id: Optional[str] = None
    gcp_location: str = "global"

    # Timeout increased to 120s to handle Vertex AI connection establishment
    # and potential network latency. The 499 "operation cancelled" errors seen
    # at ~60s suggest internal timeouts; 120s provides headroom.
    request_timeout_seconds: float = 120.0
    max_retries: int = 2
    # Backoff increased from 0.2s to 2.0s base - if a request times out,
    # retrying immediately is unlikely to help. Give the service time to recover.
    retry_backoff_base_seconds: float = 2.0
    retry_backoff_factor: float = 2.0
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_open_seconds: int = 60

    # Initialization timeout - fail fast instead of hanging forever
    # This is separate from request_timeout to catch connection establishment issues
    initialization_timeout_seconds: float = 30.0  # Model creation

    # Parallel processing settings
    # Process multiple gaps concurrently to reduce total correction time
    # Set to 1 to disable parallelism, higher values increase throughput but may hit rate limits
    max_parallel_gaps: int = 5

    @staticmethod
    def from_env(cache_dir: Optional[str] = None) -> "ProviderConfig":
        """Create config from environment variables.
        
        Args:
            cache_dir: Cache directory path. If None, uses LYRICS_TRANSCRIBER_CACHE_DIR
                      env var or defaults to ~/lyrics-transcriber-cache
        """
        if cache_dir is None:
            cache_dir = os.getenv(
                "LYRICS_TRANSCRIBER_CACHE_DIR",
                os.path.join(os.path.expanduser("~"), "lyrics-transcriber-cache")
            )
        
        return ProviderConfig(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
            privacy_mode=os.getenv("PRIVACY_MODE", "false").lower() in {"1", "true", "yes"},
            cache_dir=cache_dir,
            gcp_project_id=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
            gcp_location=os.getenv("GCP_LOCATION", "global"),
            request_timeout_seconds=float(os.getenv("AGENTIC_TIMEOUT_SECONDS", "120.0")),
            max_retries=int(os.getenv("AGENTIC_MAX_RETRIES", "2")),
            retry_backoff_base_seconds=float(os.getenv("AGENTIC_BACKOFF_BASE_SECONDS", "2.0")),
            retry_backoff_factor=float(os.getenv("AGENTIC_BACKOFF_FACTOR", "2.0")),
            circuit_breaker_failure_threshold=int(os.getenv("AGENTIC_CIRCUIT_THRESHOLD", "3")),
            circuit_breaker_open_seconds=int(os.getenv("AGENTIC_CIRCUIT_OPEN_SECONDS", "60")),
            initialization_timeout_seconds=float(os.getenv("AGENTIC_INIT_TIMEOUT_SECONDS", "30.0")),
            max_parallel_gaps=int(os.getenv("AGENTIC_MAX_PARALLEL_GAPS", "5")),
        )

    def validate_environment(self, logger: Optional[object] = None) -> None:
        """Log warnings if required keys are missing for non-privacy mode."""
        def _log(msg: str) -> None:
            try:
                if logger is not None:
                    logger.warning(msg)
                else:
                    print(msg)
            except Exception:
                pass

        if self.privacy_mode:
            return
        if not any([self.openai_api_key, self.anthropic_api_key, self.google_api_key, self.openrouter_api_key]):
            _log("No AI provider API keys configured; set PRIVACY_MODE=1 to avoid cloud usage or add provider keys.")


