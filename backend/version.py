"""
Version module for karaoke-gen backend.

Separated to avoid circular imports.
"""
import os


def get_version() -> str:
    """Get package version from environment variable, installed package, or fallback."""
    # First check environment variable (set during deployment)
    env_version = os.environ.get("KARAOKE_GEN_VERSION")
    if env_version:
        return env_version
    
    # Try to get from installed package metadata
    try:
        from importlib.metadata import version
        return version("karaoke-gen")
    except Exception:
        pass
    
    # Fallback if package not installed (e.g., during development)
    return "dev"


VERSION = get_version()

# Build metadata — set by CI during deployment
COMMIT_SHA = os.environ.get("COMMIT_SHA", "")
PR_NUMBER = os.environ.get("PR_NUMBER", "")
PR_TITLE = os.environ.get("PR_TITLE", "")
STARTUP_TIME = None  # Set on first import


def _get_startup_time() -> str:
    """Record when the backend process started (proxy for deploy time)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


STARTUP_TIME = _get_startup_time()
