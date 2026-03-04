"""
Admin API routes for blocklist and YouTube queue management.

Handles:
- Blocklist management (disposable domains, blocked emails, blocked IPs)
- YouTube upload queue management
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult
from backend.services.email_validation_service import get_email_validation_service, EmailValidationService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/rate-limits", tags=["admin", "rate-limits"])


# =============================================================================
# Response Models
# =============================================================================

class YouTubeQueueEntry(BaseModel):
    """A queued YouTube upload entry."""
    job_id: str
    status: str
    reason: Optional[str] = None
    user_email: Optional[str] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    brand_code: Optional[str] = None
    queued_at: Optional[datetime] = None
    attempts: int = 0
    max_attempts: int = 5
    last_error: Optional[str] = None
    youtube_url: Optional[str] = None
    notification_sent: bool = False


class YouTubeQueueListResponse(BaseModel):
    """List of YouTube upload queue entries."""
    entries: List[YouTubeQueueEntry]
    stats: dict


class BlocklistsResponse(BaseModel):
    """All blocklist data."""
    disposable_domains: List[str]
    blocked_emails: List[str]
    blocked_ips: List[str]
    updated_at: Optional[datetime]
    updated_by: Optional[str]


class DomainRequest(BaseModel):
    """Request to add/remove a domain."""
    domain: str


class EmailRequest(BaseModel):
    """Request to add/remove an email."""
    email: str


class IPRequest(BaseModel):
    """Request to add/remove an IP."""
    ip_address: str


class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool
    message: str


# =============================================================================
# Blocklist Management Endpoints
# =============================================================================

@router.get("/blocklists", response_model=BlocklistsResponse)
async def get_blocklists(
    auth_result: AuthResult = Depends(require_admin),
):
    """
    Get all blocklist data.

    Returns disposable domains, blocked emails, and blocked IPs.
    """
    email_validation = get_email_validation_service()

    # Force refresh to get latest data
    config = email_validation.get_blocklist_config(force_refresh=True)

    # Get the raw document for metadata
    from google.cloud import firestore
    from backend.services.firestore_service import get_firestore_client
    from backend.services.email_validation_service import BLOCKLISTS_COLLECTION, BLOCKLIST_CONFIG_DOC

    db = get_firestore_client()
    doc = db.collection(BLOCKLISTS_COLLECTION).document(BLOCKLIST_CONFIG_DOC).get()

    updated_at = None
    updated_by = None
    if doc.exists:
        data = doc.to_dict()
        updated_at = data.get("updated_at")
        updated_by = data.get("updated_by")

    return BlocklistsResponse(
        disposable_domains=sorted(list(config["disposable_domains"])),
        blocked_emails=sorted(list(config["blocked_emails"])),
        blocked_ips=sorted(list(config["blocked_ips"])),
        updated_at=updated_at,
        updated_by=updated_by,
    )


@router.post("/blocklists/disposable-domains", response_model=SuccessResponse)
async def add_disposable_domain(
    request: DomainRequest,
    auth_result: AuthResult = Depends(require_admin),
):
    """Add a domain to the disposable domains blocklist."""
    email_validation = get_email_validation_service()

    domain = request.domain.lower().strip()
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="Invalid domain format")

    email_validation.add_disposable_domain(domain, auth_result.user_email)

    return SuccessResponse(
        success=True,
        message=f"Domain '{domain}' added to disposable domains blocklist"
    )


@router.delete("/blocklists/disposable-domains/{domain}", response_model=SuccessResponse)
async def remove_disposable_domain(
    domain: str,
    auth_result: AuthResult = Depends(require_admin),
):
    """Remove a domain from the disposable domains blocklist."""
    email_validation = get_email_validation_service()

    domain = domain.lower().strip()
    if not email_validation.remove_disposable_domain(domain, auth_result.user_email):
        raise HTTPException(status_code=404, detail="Domain not found in blocklist")

    return SuccessResponse(
        success=True,
        message=f"Domain '{domain}' removed from disposable domains blocklist"
    )


@router.post("/blocklists/blocked-emails", response_model=SuccessResponse)
async def add_blocked_email(
    request: EmailRequest,
    auth_result: AuthResult = Depends(require_admin),
):
    """Add an email to the blocked emails list."""
    email_validation = get_email_validation_service()

    email = request.email.lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email format")

    email_validation.add_blocked_email(email, auth_result.user_email)

    return SuccessResponse(
        success=True,
        message=f"Email '{email}' added to blocked emails list"
    )


@router.delete("/blocklists/blocked-emails/{email}", response_model=SuccessResponse)
async def remove_blocked_email(
    email: str,
    auth_result: AuthResult = Depends(require_admin),
):
    """Remove an email from the blocked emails list."""
    email_validation = get_email_validation_service()

    email = email.lower().strip()
    if not email_validation.remove_blocked_email(email, auth_result.user_email):
        raise HTTPException(status_code=404, detail="Email not found in blocklist")

    return SuccessResponse(
        success=True,
        message=f"Email '{email}' removed from blocked emails list"
    )


@router.post("/blocklists/blocked-ips", response_model=SuccessResponse)
async def add_blocked_ip(
    request: IPRequest,
    auth_result: AuthResult = Depends(require_admin),
):
    """Add an IP address to the blocked IPs list."""
    email_validation = get_email_validation_service()

    ip_address = request.ip_address.strip()
    if not ip_address:
        raise HTTPException(status_code=400, detail="Invalid IP address")

    email_validation.add_blocked_ip(ip_address, auth_result.user_email)

    return SuccessResponse(
        success=True,
        message=f"IP '{ip_address}' added to blocked IPs list"
    )


@router.delete("/blocklists/blocked-ips/{ip_address}", response_model=SuccessResponse)
async def remove_blocked_ip(
    ip_address: str,
    auth_result: AuthResult = Depends(require_admin),
):
    """Remove an IP address from the blocked IPs list."""
    email_validation = get_email_validation_service()

    ip_address = ip_address.strip()
    if not email_validation.remove_blocked_ip(ip_address, auth_result.user_email):
        raise HTTPException(status_code=404, detail="IP not found in blocklist")

    return SuccessResponse(
        success=True,
        message=f"IP '{ip_address}' removed from blocked IPs list"
    )


# =============================================================================
# YouTube Upload Queue Management Endpoints
# =============================================================================

@router.get("/youtube-queue", response_model=YouTubeQueueListResponse)
async def get_youtube_queue(
    auth_result: AuthResult = Depends(require_admin),
):
    """
    Get YouTube upload queue entries and stats.

    Returns all queue entries (any status) and aggregate statistics.
    """
    from backend.services.youtube_upload_queue_service import get_youtube_upload_queue_service

    queue_service = get_youtube_upload_queue_service()
    entries = queue_service.get_all_queue_entries(limit=50)
    stats = queue_service.get_queue_stats()

    return YouTubeQueueListResponse(
        entries=[YouTubeQueueEntry(**entry) for entry in entries],
        stats=stats,
    )


@router.post("/youtube-queue/{job_id}/retry", response_model=SuccessResponse)
async def retry_youtube_upload(
    job_id: str,
    auth_result: AuthResult = Depends(require_admin),
):
    """
    Manually retry a failed YouTube upload.

    Resets the entry back to 'queued' status with 0 attempts.
    """
    from backend.services.youtube_upload_queue_service import get_youtube_upload_queue_service

    queue_service = get_youtube_upload_queue_service()

    if not queue_service.retry_upload(job_id):
        raise HTTPException(
            status_code=404,
            detail=f"Upload not found or not in retryable state for job {job_id}"
        )

    return SuccessResponse(
        success=True,
        message=f"YouTube upload for job {job_id} queued for retry"
    )


@router.post("/youtube-queue/process", response_model=SuccessResponse)
async def trigger_youtube_queue_processing(
    auth_result: AuthResult = Depends(require_admin),
):
    """
    Manually trigger YouTube upload queue processing.

    Same as the scheduled processor but triggered from the admin dashboard.
    Runs in the background and returns immediately.
    """
    from backend.workers.youtube_queue_processor import process_youtube_upload_queue
    import asyncio

    async def _process():
        try:
            result = await process_youtube_upload_queue()
            logger.info(f"Manual YouTube queue processing complete: {result}")
        except Exception as e:
            logger.exception(f"Manual YouTube queue processing failed: {e}")

    # Fire and forget
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_process())
    except RuntimeError:
        pass

    return SuccessResponse(
        success=True,
        message="YouTube queue processing started in background"
    )
