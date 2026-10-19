"""HTTP access to LRCLIB (https://lrclib.net/docs#implementation-requirements-rate-limiting).

Unregistered clients get ~45-90 requests/min per IP, and Cloud Run shares egress IPs, so
since Oct 2026 our anonymous requests were intermittently rejected (503s). We are a
registered server-side application: send ``Lrclib-App-Id`` + the secret ``Lrclib-Api-Key``
(env ``LRCLIB_APP_ID`` / ``LRCLIB_SECRET_KEY``) for the app-wide allowance.

LRCLIB requires clients to honour ``Retry-After`` on 429 (plus up to 30% jitter) or risk a
temporary ban, so ``get`` does that, retries 5xx with a short backoff, and drops the
credentials for the rest of the process if they are rejected (401) so a revoked key
degrades to anonymous access instead of losing LRCLIB entirely.
"""
import logging
import os
import random
import time
from typing import Any, Dict, Optional

import requests

DEFAULT_USER_AGENT = "karaoke-gen (https://github.com/nomadkaraoke/karaoke-gen)"
MAX_RETRY_AFTER_S = 30.0
SERVER_ERROR_BACKOFF_S = (1.0, 2.0)

_credentials_rejected = False


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
    try:
        base = float(response.headers.get("Retry-After", "1"))
    except ValueError:
        base = 1.0
    return min(max(base, 0.0) * (1 + random.uniform(0, 0.3)), MAX_RETRY_AFTER_S)


def get(
    url: str,
    params: Dict[str, Any],
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 15,
    logger: Optional[logging.Logger] = None,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """GET an LRCLIB endpoint with auth, Retry-After handling and 5xx retries.

    Returns the final response (callers check the status); raises only on transport errors.
    """
    global _credentials_rejected
    log = logger or logging.getLogger(__name__)
    http = session or requests
    server_error_retries = list(SERVER_ERROR_BACKOFF_S)
    rate_limit_retried = False
    while True:
        sent_auth = auth_headers()
        response = http.get(url, params=params, timeout=timeout,
                            headers={"User-Agent": user_agent, **sent_auth})
        status = response.status_code
        if status == 401 and sent_auth:
            _credentials_rejected = True
            log.warning("LRCLIB rejected our app credentials (401); continuing anonymously. "
                        "Check LRCLIB_APP_ID / LRCLIB_SECRET_KEY in the LRCLIB developer portal.")
            continue
        if status == 429 and not rate_limit_retried:
            rate_limit_retried = True
            delay = _retry_after_seconds(response)
            log.info(f"LRCLIB rate limited (429); retrying in {delay:.1f}s")
            time.sleep(delay)
            continue
        if status >= 500 and server_error_retries:
            delay = server_error_retries.pop(0)
            log.info(f"LRCLIB returned {status}; retrying in {delay:.0f}s")
            time.sleep(delay)
            continue
        return response
