"""LangFuse integration for agentic correction observability and prompt management.

This module provides:
- Client initialization with fail-fast behavior when configured
- Metrics recording for observability
- Prompt fetching for dynamic prompt management
- Dataset fetching for few-shot examples
"""

from typing import Optional, Dict, Any, List
import os
import logging

logger = logging.getLogger(__name__)

# Module-level client singleton
_langfuse_client: Optional[Any] = None
_client_initialized: bool = False


class LangFuseConfigError(Exception):
    """Raised when LangFuse is configured but initialization fails."""
    pass


def is_langfuse_configured() -> bool:
    """Check if LangFuse credentials are configured in environment."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    return bool(public_key and secret_key)


def setup_langfuse() -> Optional[object]:
    """Initialize Langfuse client if keys are present; return client or None.

    This avoids hard dependency at import time; caller can check for None and
    no-op if observability is not configured.

    Note: This function does NOT fail fast - use get_langfuse_client() for
    fail-fast behavior when LangFuse is required.
    """
    secret = os.getenv("LANGFUSE_SECRET_KEY")
    public = os.getenv("LANGFUSE_PUBLIC_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
    if not (secret and public):
        return None
    try:
        from langfuse import Langfuse  # type: ignore

        client = Langfuse(secret_key=secret, public_key=public, host=host)
        return client
    except Exception:
        return None


def get_langfuse_client() -> Optional[Any]:
    """Get or create the LangFuse client singleton.

    Unlike setup_langfuse(), this function implements fail-fast behavior:
    if LangFuse keys are configured but initialization fails, it raises
    an exception rather than returning None.

    Returns:
        Langfuse client instance, or None if not configured

    Raises:
        LangFuseConfigError: If keys are set but initialization fails
    """
    global _langfuse_client, _client_initialized

    if _client_initialized:
        return _langfuse_client

    secret = os.getenv("LANGFUSE_SECRET_KEY")
    public = os.getenv("LANGFUSE_PUBLIC_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")

    if not (secret and public):
        logger.debug("LangFuse keys not configured, client disabled")
        _client_initialized = True
        return None

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            secret_key=secret,
            public_key=public,
            host=host,
        )
        _client_initialized = True
        logger.info(f"LangFuse client initialized (host: {host})")
        return _langfuse_client

    except Exception as e:
        # Fail fast - if keys are set, we expect LangFuse to work
        raise LangFuseConfigError(
            f"LangFuse keys are set but initialization failed: {e}\n"
            f"Check:\n"
            f"  - LANGFUSE_PUBLIC_KEY: {public[:10] if public else 'not set'}...\n"
            f"  - LANGFUSE_SECRET_KEY: {'set' if secret else 'not set'}\n"
            f"  - LANGFUSE_HOST: {host}"
        ) from e


def reset_langfuse_client() -> None:
    """Reset the global LangFuse client (for testing)."""
    global _langfuse_client, _client_initialized
    _langfuse_client = None
    _client_initialized = False


def record_metrics(client: Optional[object], name: str, metrics: Dict[str, Any]) -> None:
    """Record custom metrics to Langfuse if initialized."""
    if client is None:
        return
    try:
        # Minimal shape to avoid strict coupling; callers can extend
        client.trace(name=name, metadata=metrics)
    except Exception:
        # Swallow observability errors to never impact core flow
        pass


def fetch_prompt(name: str, client: Optional[Any] = None, label: Optional[str] = "production") -> Any:
    """Fetch a prompt template from LangFuse.

    Args:
        name: The prompt name in LangFuse
        client: Optional pre-initialized client. If None, uses get_langfuse_client()
        label: Prompt label to fetch (default: "production"). If the labeled version
               is not found, falls back to version 1.

    Returns:
        LangFuse prompt object

    Raises:
        LangFuseConfigError: If LangFuse is not configured
        RuntimeError: If prompt fetch fails
    """
    if client is None:
        client = get_langfuse_client()

    if client is None:
        raise LangFuseConfigError(
            f"Cannot fetch prompt '{name}': LangFuse is not configured. "
            f"Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY."
        )

    try:
        # Try to fetch with the specified label (default: production)
        prompt = client.get_prompt(name, label=label)
        logger.debug(f"Fetched prompt '{name}' (label={label}) from LangFuse")
        return prompt
    except Exception as label_error:
        # If labeled version not found, try fetching version 1 as fallback
        # This handles newly created prompts that haven't been promoted yet
        try:
            prompt = client.get_prompt(name, version=1)
            logger.warning(
                f"Prompt '{name}' label '{label}' not found, using version 1. "
                f"Consider promoting this prompt in LangFuse UI."
            )
            return prompt
        except Exception as version_error:
            raise RuntimeError(
                f"Failed to fetch prompt '{name}' from LangFuse: "
                f"Label '{label}' error: {label_error}, "
                f"Version 1 fallback error: {version_error}"
            ) from version_error


def fetch_dataset(name: str, client: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Fetch a dataset from LangFuse and return its items.

    Args:
        name: The dataset name in LangFuse
        client: Optional pre-initialized client. If None, uses get_langfuse_client()

    Returns:
        List of dataset item inputs (the actual example data)

    Raises:
        LangFuseConfigError: If LangFuse is not configured
        RuntimeError: If dataset fetch fails
    """
    if client is None:
        client = get_langfuse_client()

    if client is None:
        raise LangFuseConfigError(
            f"Cannot fetch dataset '{name}': LangFuse is not configured. "
            f"Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY."
        )

    try:
        dataset = client.get_dataset(name)
        items = []
        for item in dataset.items:
            if hasattr(item, 'input') and item.input:
                items.append(item.input)

        logger.debug(f"Fetched {len(items)} items from dataset '{name}'")
        return items
    except Exception as e:
        raise RuntimeError(
            f"Failed to fetch dataset '{name}' from LangFuse: {e}"
        ) from e
