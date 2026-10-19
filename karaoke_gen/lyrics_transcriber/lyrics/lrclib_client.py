"""HTTP access to LRCLIB (https://lrclib.net/docs#implementation-requirements-rate-limiting).

Unregistered clients get ~45-90 requests/min per IP, and Cloud Run shares egress IPs, so
since Oct 2026 our anonymous requests were intermittently rejected (503s). We are a
registered server-side application: send ``Lrclib-App-Id`` + the secret ``Lrclib-Api-Key``
(env ``LRCLIB_APP_ID`` / ``LRCLIB_SECRET_KEY``) for the app-wide allowance.

LRCLIB requires clients to honour ``Retry-After`` on 429 (plus up to 30% jitter) or risk a
temporary ban. ``get`` records a process-wide cooldown from every 429 and sends nothing
until it has passed (callers get the 429 back instead); it waits and retries once only
when the wait fits ``MAX_WAIT_S``. It also retries 5xx with a short backoff, and drops the
credentials for the rest of the process if they are rejected (401) so a revoked key
degrades to anonymous access instead of losing LRCLIB entirely.
"""
import logging
import os
import random
import threading
import time
from typing import Any, Dict, Optional

import requests

DEFAULT_USER_AGENT = "karaoke-gen (https://github.com/nomadkaraoke/karaoke-gen)"
MAX_WAIT_S = 30.0  # longest we block one call for a Retry-After; longer cooldowns skip LRCLIB
SERVER_ERROR_BACKOFF_S = (1.0, 2.0)

_credentials_rejected = False
_cooldown_until = 0.0  # time.monotonic() before which we must not call LRCLIB (Retry-After)
_cooldown_response: Optional[requests.Response] = None
_cooldown_lock = threading.Lock()


def auth_headers() -> Dict[str, str]:
    """Registered-app headers, or {} when not configured or rejected earlier."""
    if _credentials_rejected:
        return {}
    headers = {}
    app_id = os.environ.get("LRCLIB_APP_ID")
    api_key = os.environ.get("LRCLIB_SECRET_KEY")
    if app_id:
        headers["Lrclib-App-Id"] = app_id
    if api_key:
        headers["Lrclib-Api-Key"] = api_key
    return headers


def _retry_after_seconds(response: requests.Response) -> float:
    """Retry-After plus up to 30% jitter (never shortened)."""
    try:
        base = float(response.headers.get("Retry-After", "1"))
    except ValueError:
        base = 1.0
    return max(base, 0.0) * (1 + random.uniform(0, 0.3))


def get(
    url: str,
    params: Dict[str, Any],
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 15,
    logger: Optional[logging.Logger] = None,
    session: Optional[requests.Session] = None,
    retry: bool = True,
) -> requests.Response:
    """GET an LRCLIB endpoint with auth, Retry-After cooldown and (if ``retry``) 5xx retries.

    Returns the final response (callers check the status); raises only on transport errors.
    While a Retry-After cooldown is active, returns the 429 that started it without sending.
    """
    global _credentials_rejected, _cooldown_until, _cooldown_response
    log = logger or logging.getLogger(__name__)
    http = session or requests
    server_error_retries = list(SERVER_ERROR_BACKOFF_S) if retry else []
    rate_limit_retried = not retry
    while True:
        wait = _cooldown_until - time.monotonic()
        if wait > 0:
            if rate_limit_retried or wait > MAX_WAIT_S:
                log.info(f"LRCLIB cooldown active ({wait:.0f}s left after a 429); skipping request")
                return _cooldown_response
            rate_limit_retried = True
            time.sleep(wait)
        sent_auth = auth_headers()
        response = http.get(url, params=params, timeout=timeout,
                            headers={"User-Agent": user_agent, **sent_auth})
        status = response.status_code
        if status == 401 and sent_auth:
            _credentials_rejected = True
            log.warning("LRCLIB rejected our app credentials (401); continuing anonymously. "
                        "Check LRCLIB_APP_ID / LRCLIB_SECRET_KEY in the LRCLIB developer portal.")
            continue
        if status == 429:
            delay = _retry_after_seconds(response)
            with _cooldown_lock:  # overlapping 429s: keep the latest expiry, never shorten it
                if time.monotonic() + delay > _cooldown_until:
                    _cooldown_until = time.monotonic() + delay
                    _cooldown_response = response
            log.info(f"LRCLIB rate limited (429); cooling down {delay:.1f}s")
            if rate_limit_retried or delay > MAX_WAIT_S:
                return response
            continue  # the cooldown check at the top of the loop waits, then retries once
        if status >= 500 and server_error_retries:
            delay = server_error_retries.pop(0)
            if "Retry-After" in response.headers:  # an overloaded 503 may say how long to wait
                delay = max(delay, _retry_after_seconds(response))
                if delay > MAX_WAIT_S:
                    return response
            log.info(f"LRCLIB returned {status}; retrying in {delay:.0f}s")
            time.sleep(delay)
            continue
        return response
