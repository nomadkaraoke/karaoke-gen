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
