"""Shared Gemini client for the backend: Gemini Developer API (API key), not Vertex.

Every backend Gemini call goes through :func:`get_genai_client`. Vertex AI is
disabled in the nomadkaraoke GCP project so AI spend never lands on it; calls are
billed to whichever AI Studio key is stored in Secret Manager ``gemini-api-key``.

Key resolution (via :meth:`backend.config.Settings.get_secret`):
  1. ``GEMINI_API_KEY`` env var — injected on Cloud Run from
     ``gemini-api-key:latest`` (CI ``--set-secrets`` for the service, Pulumi
     for the jobs).
  2. Secret Manager ``gemini-api-key`` (latest) — read directly with the
     runtime service account / your ADC when the env var isn't set.

Rotating the key: add a new version of ``gemini-api-key``. New instances / job
executions pick it up; no code change.

Degradation: when the key's quota or prepaid credit is exhausted (or the key is
rejected), callers must degrade rather than fail jobs. Use
:func:`is_quota_or_billing_error` to classify and :func:`note_gemini_failure`
from the caller's ``except`` block — it also sends a Discord ops alert at most
once per ``GEMINI_QUOTA_ALERT_HOURS`` (default 6h) across all instances.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

SECRET_NAME = "gemini-api-key"

QUOTA_EXHAUSTED_MESSAGE = (
    "Gemini quota/credit exhausted — top up AI Studio or rotate gemini-api-key "
    "(add a new version of Secret Manager secret 'gemini-api-key' in project nomadkaraoke)."
)

# Firestore dedup doc id (in ops_alerts' collection) shared by every instance/job.
_ALERT_KEY = "gemini-quota-exhausted"
_DEFAULT_ALERT_HOURS = 6.0


class GeminiKeyUnavailableError(RuntimeError):
    """No Gemini API key could be resolved (env var unset and Secret Manager failed)."""


def get_api_key() -> str:
    """Return the Gemini Developer API key or raise GeminiKeyUnavailableError."""
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    from backend.config import get_settings

    key = (get_settings().get_secret(SECRET_NAME) or "").strip()
    if not key:
        raise GeminiKeyUnavailableError(
            "GEMINI_API_KEY is not set and Secret Manager secret 'gemini-api-key' "
            "could not be read"
        )
    return key


def get_genai_client(timeout_ms: Optional[int] = None) -> Any:
    """Build a google-genai client for the Gemini Developer API.

    ``timeout_ms`` bounds each HTTP request (milliseconds). No project/location:
    those only apply to Vertex AI.
    """
    from google import genai
    from google.genai import types

    http_options = types.HttpOptions(timeout=int(timeout_ms)) if timeout_ms else None
    return genai.Client(api_key=get_api_key(), http_options=http_options)


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


def is_quota_or_billing_error(exc: Optional[BaseException]) -> bool:
    """True if ``exc`` (or anything in its cause chain) is a Gemini quota,
    prepaid-credit, billing or API-key error — i.e. needs a top-up/key rotation,
    not a retry.

    Matches google-genai ``APIError`` (``.code`` 429 / 403 or status
    RESOURCE_EXHAUSTED / PERMISSION_DENIED, or a billing/key message on a 400),
    a missing key, and wrapped/stringified errors from other layers (LangChain).
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


def _alert_hours() -> float:
    try:
        return float(os.environ.get("GEMINI_QUOTA_ALERT_HOURS", _DEFAULT_ALERT_HOURS))
    except (TypeError, ValueError):
        return _DEFAULT_ALERT_HOURS


# In-process throttle so a burst of failures in one instance doesn't hit
# Firestore on every call; the Firestore doc throttles across instances/jobs.
_last_alert_attempt: float = 0.0


def note_gemini_failure(caller: str, exc: BaseException) -> bool:
    """Classify a Gemini failure; on quota/billing/key errors log + alert.

    Call from a caller's ``except`` block. Returns True when the error is a
    quota/billing/key error (caller should skip retries and degrade). Never raises.
    """
    try:
        if not is_quota_or_billing_error(exc):
            return False
        logger.error("Gemini unavailable (quota/billing/key) in %s: %s", caller, exc)
        _maybe_alert(caller, exc)
        return True
    except Exception:  # noqa: BLE001 - classification/alerting must never raise
        logger.warning("note_gemini_failure failed", exc_info=True)
        return False


def _maybe_alert(caller: str, exc: BaseException) -> None:
    global _last_alert_attempt
    window = _alert_hours() * 3600
    now = time.monotonic()
    if _last_alert_attempt and now - _last_alert_attempt < window:
        return
    _last_alert_attempt = now
    from backend.services import ops_alerts

    err = str(exc).strip()
    if len(err) > 800:
        err = err[:800] + " …[truncated]"
    message = (
        "🟠 **Gemini API unavailable — quota / prepaid credit / key problem**\n"
        f"**First seen in:** `{caller}`\n"
        "Callers are degrading (auto-correct keeps Opus only, match-judge/parse-titles "
        "return no suggestion, credit evals go to manual review, translations are "
        "skipped).\n"
        f"**Fix:** {QUOTA_EXHAUSTED_MESSAGE}\n"
        f"**Error:** ```{err}```"
    )
    ops_alerts.send_throttled_ops_alert(_ALERT_KEY, message, throttle_minutes=int(window / 60))
