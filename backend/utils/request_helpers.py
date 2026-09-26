"""
Request utility helpers for extracting client information.
"""
from typing import Optional
from fastapi import Request


def get_client_ip(request: Request) -> Optional[str]:
    """
    Extract real client IP address from a request.

    Cloud Run and load balancers set X-Forwarded-For header with the
    original client IP as the first entry. Falls back to request.client.host
    for direct connections (local dev).

    Returns None if no IP can be determined.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


# Headers never worth recording, even redacted (already captured elsewhere / noise).
_SKIPPED_CUSTOM_HEADERS = frozenset({"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"})

# Any x-* header whose name contains one of these carries a credential
# (x-kjbox-secret, x-admin-token, x-kh-forwarder-secret, x-e2e-bypass-key,
# x-api-key, x-authorization, ...). request_metadata.custom_headers is returned
# to the job owner by GET /api/jobs/{id}, so these must never be stored raw.
_SECRET_HEADER_MARKERS = ("secret", "token", "key", "auth", "password", "passwd", "cookie", "signature", "credential", "bypass", "jwt", "session")
REDACTED = "[redacted]"


def is_secret_header(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _SECRET_HEADER_MARKERS)


def collect_custom_headers(headers) -> dict:
    """All X-* request headers for job request_metadata, with secret-bearing
    header values replaced by ``"[redacted]"`` (the name is kept for debugging)."""
    custom = {}
    for key, value in dict(headers).items():
        lowered = key.lower()
        if not lowered.startswith("x-") or lowered in _SKIPPED_CUSTOM_HEADERS:
            continue
        custom[key] = REDACTED if is_secret_header(lowered) else value
    return custom
