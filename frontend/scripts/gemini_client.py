"""Gemini Developer API (Google AI Studio) client helper for local scripts.

All Gemini calls go through the Developer API with an API key, NOT Vertex AI
(Vertex is disabled in the nomadkaraoke GCP project to keep AI spend off it).

Key resolution (first match wins):
  1. ``GEMINI_API_KEY`` environment variable
  2. ``gcloud secrets versions access latest --secret=gemini-api-key
     --project=nomadkaraoke`` (your own gcloud login)

The key is cached in-process only; it is never written to disk or printed.

Rotating the key (e.g. prepaid credit exhausted): add a new secret version —
no code change needed:
  printf '%s' "$NEW_KEY" | gcloud secrets versions add gemini-api-key \
      --project=nomadkaraoke --data-file=-
"""

from __future__ import annotations

import os
import re
import subprocess

SECRET_NAME = "gemini-api-key"
SECRET_PROJECT = "nomadkaraoke"

QUOTA_EXHAUSTED_MESSAGE = (
    "Gemini quota/credit exhausted — top up AI Studio or rotate gemini-api-key "
    f"(add a new version of Secret Manager secret '{SECRET_NAME}' in project "
    f"'{SECRET_PROJECT}')."
)


def quota_exhausted_message() -> str:
    """QUOTA_EXHAUSTED_MESSAGE plus a hint when the key came from the environment."""
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return (
            QUOTA_EXHAUSTED_MESSAGE
            + " Note: the key came from the GEMINI_API_KEY environment variable — "
            "unset it to use the Secret Manager key instead."
        )
    return QUOTA_EXHAUSTED_MESSAGE


class GeminiKeyUnavailableError(RuntimeError):
    """The Gemini API key could not be resolved (env var unset and gcloud failed)."""


_cached_key: str | None = None


def get_api_key() -> str:
    """Return the Gemini Developer API key (env var, else Secret Manager via gcloud)."""
    global _cached_key
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    if _cached_key:
        return _cached_key
    cmd = [
        "gcloud", "secrets", "versions", "access", "latest",
        f"--secret={SECRET_NAME}", f"--project={SECRET_PROJECT}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GeminiKeyUnavailableError(
            f"GEMINI_API_KEY is not set and `gcloud secrets versions access` failed: {e}"
        ) from e
    key = (result.stdout or "").strip()
    if result.returncode != 0 or not key:
        stderr = (result.stderr or "").strip()[-300:]
        raise GeminiKeyUnavailableError(
            "GEMINI_API_KEY is not set and reading Secret Manager secret "
            f"'{SECRET_NAME}' (project {SECRET_PROJECT}) failed — run `gcloud auth login` "
            f"or export GEMINI_API_KEY. gcloud said: {stderr}"
        )
    _cached_key = key
    return key


def get_genai_client():
    """Build a google-genai client for the Gemini Developer API."""
    from google import genai

    return genai.Client(api_key=get_api_key())


# Message fragments that mean "the key/account can't pay or isn't valid" — a
# retry won't help; a human must top up credit or rotate the key.
_QUOTA_PATTERNS = re.compile(
    r"RESOURCE_EXHAUSTED|exceeded your current quota|quota exceeded|"
    r"billing (details|account)|prepa(id|yment)|credits? (are |is |have been )?"
    r"(exhausted|depleted|used up)|insufficient (credit|funds|balance)|"
    r"API_KEY_INVALID|API key not valid|API key expired|API_KEY_SERVICE_BLOCKED|"
    r"\b429 Too Many Requests",
    re.IGNORECASE,
)

# A 429 that names a per-minute quota / carries a retry delay is an ordinary
# rate limit (transient — retry with backoff), not exhausted credit.
_RATE_LIMIT_PATTERNS = re.compile(r"PerMinute|retryDelay|retry in \d", re.IGNORECASE)


def is_quota_or_billing_error(exc: BaseException | None) -> bool:
    """True if ``exc`` (or anything in its cause chain) is a Gemini quota,
    prepaid-credit, billing or API-key error — i.e. needs a top-up/key rotation,
    not a retry.

    Matches google-genai ``APIError`` (``.code`` 429 / 403, or 400 with a
    billing/key message) and wrapped/stringified errors from other layers.
    Per-minute rate limits (429 naming a ``PerMinute`` quota or a retry delay)
    are NOT matched — those are transient; retry them.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, GeminiKeyUnavailableError):
            return True
        code = getattr(exc, "code", None)
        if code is None:
            code = getattr(exc, "status_code", None)
        status = str(getattr(exc, "status", "") or "")
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return not _RATE_LIMIT_PATTERNS.search(str(exc))
        if code == 403 or status == "PERMISSION_DENIED":
            return True
        text = str(exc)
        if _QUOTA_PATTERNS.search(text) and not _RATE_LIMIT_PATTERNS.search(text):
            return True
        exc = exc.__cause__ or exc.__context__
    return False
