"""
FastAPI dependencies for authentication and authorization.
"""
import logging
import secrets
from datetime import datetime, timedelta, UTC
from typing import Optional, Tuple, Callable, Union
from fastapi import Depends, HTTPException, Header, Query, Request, Path
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from backend.services.auth_service import get_auth_service, UserType, AuthService, AuthResult


logger = logging.getLogger(__name__)


def generate_review_token() -> str:
    """Generate a cryptographically secure review token."""
    return secrets.token_urlsafe(32)


def get_review_token_expiry(hours: int = 24) -> datetime:
    """Get expiry time for a review token."""
    return datetime.now(UTC) + timedelta(hours=hours)


# HTTP Bearer security scheme (Authorization: Bearer <token>)
security = HTTPBearer(auto_error=False)


async def get_token_from_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    token: Optional[str] = Query(None, description="Access token (alternative to Bearer header)")
) -> Optional[str]:
    """
    Extract access token from request.
    
    Supports three methods (in priority order):
    1. X-Admin-Token header: Used by Cloud Tasks (since OIDC overwrites Authorization)
    2. Authorization header: Authorization: Bearer <token>
    3. Query parameter: ?token=<token> (for download links)
    
    Returns:
        Token string or None
    """
    # Check X-Admin-Token header first (used by Cloud Tasks)
    # Cloud Tasks OIDC token overwrites Authorization header, so we use a custom header
    admin_token = request.headers.get("X-Admin-Token")
    if admin_token:
        return admin_token
    
    # Try Authorization header
    if credentials:
        return credentials.credentials
    
    # Try query parameter
    if token:
        return token
    
    return None


async def require_auth(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    token: Optional[str] = Query(None)
) -> AuthResult:
    """
    Require authentication for an endpoint.

    Returns:
        AuthResult with full authentication context including:
        - is_valid, user_type, remaining_uses, message (backward compatible via tuple unpacking)
        - user_email: Email of authenticated user (if session/API key auth)
        - is_admin: Whether user has admin privileges

    Raises:
        HTTPException: 401 if authentication fails
    """
    # Get token from request
    token_str = await get_token_from_request(request, credentials, token)

    if not token_str:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide token via Authorization header or ?token= parameter"
        )

    # Validate token using the full method
    auth_result = auth_service.validate_token_full(token_str)

    # Get request_id from middleware for correlation
    request_id = getattr(request.state, "request_id", None)

    if not auth_result.is_valid:
        # Log auth failure with request_id for correlation
        auth_header = request.headers.get("Authorization", "")
        logger.warning(
            "auth_failed",
            extra={
                "request_id": request_id,
                "audit_type": "auth_event",
                "auth_message": auth_result.message,
                "token_provided": bool(token_str),
                "token_length": len(token_str) if token_str else 0,
                "auth_header_present": bool(auth_header),
            }
        )
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {auth_result.message}"
        )

    # Log successful auth with request_id for correlation with request audit
    logger.info(
        "auth_success",
        extra={
            "request_id": request_id,
            "user_email": auth_result.user_email,
            "user_type": auth_result.user_type.value if auth_result.user_type else None,
            "is_admin": auth_result.is_admin,
            "remaining_uses": auth_result.remaining_uses,
            "audit_type": "auth_event",
        }
    )

    return auth_result


async def require_auth_legacy(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    token: Optional[str] = Query(None)
) -> Tuple[str, UserType, int]:
    """
    Legacy authentication dependency that returns tuple.

    DEPRECATED: Use require_auth which returns AuthResult instead.

    Returns:
        (token, user_type, remaining_uses)
    """
    token_str = await get_token_from_request(request, credentials, token)

    if not token_str:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide token via Authorization header or ?token= parameter"
        )

    is_valid, user_type, remaining_uses, message = auth_service.validate_token(token_str)

    if not is_valid:
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {message}"
        )

    return token_str, user_type, remaining_uses


async def require_admin(
    auth_result: AuthResult = Depends(require_auth)
) -> AuthResult:
    """
    Require admin access for an endpoint.

    Admin access is granted if:
    - Using an admin token from ADMIN_TOKENS env var
    - User email is from admin domain (e.g., @nomadkaraoke.com)
    - User role is set to ADMIN in database

    Returns:
        AuthResult with admin privileges

    Raises:
        HTTPException: 403 if user is not admin
    """
    if not auth_result.is_admin:
        logger.warning(
            f"Admin access denied for {auth_result.user_type} user "
            f"(email: {auth_result.user_email or 'unknown'})"
        )
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )

    return auth_result


def optional_auth(
    auth_service: AuthService = Depends(get_auth_service),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    token: Optional[str] = Query(None)
) -> Optional[Tuple[str, UserType, int]]:
    """
    Optional authentication - doesn't fail if no token provided.
    
    Useful for endpoints that have different behavior for authenticated users.
    
    Returns:
        (token, user_type, remaining_uses) if authenticated, None otherwise
    """
    # Get token
    token_str = None
    if credentials:
        token_str = credentials.credentials
    elif token:
        token_str = token
    
    if not token_str:
        return None
    
    # Validate token
    is_valid, user_type, remaining_uses, message = auth_service.validate_token(token_str)
    
    if not is_valid:
        logger.debug(f"Optional auth failed: {message}")
        return None
    
    return token_str, user_type, remaining_uses


def require_review_auth_factory(job_id_param: str = "job_id"):
    """
    Factory to create a review authentication dependency.

    Accepts either:
    1. Full user authentication (admin/user token) - also validates job ownership
    2. Job-specific review token (only valid for the specific job)

    Args:
        job_id_param: Name of the path parameter containing the job ID

    Returns:
        Dependency function that validates review access
    """
    async def require_review_auth(
        request: Request,
        job_id: str = Path(...),
        review_token: Optional[str] = Query(None, description="Job-specific review token"),
        auth_service: AuthService = Depends(get_auth_service),
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
        token: Optional[str] = Query(None, alias="token", description="Full access token")
    ) -> Tuple[str, str]:
        """
        Validate review access for a job.

        Returns:
            (job_id, auth_type) where auth_type is "full" or "review_token"

        Raises:
            HTTPException: 401 if authentication fails
            HTTPException: 403 if user doesn't own the job (for full auth)
        """
        # Import here to avoid circular dependency
        from backend.services.job_manager import JobManager

        job_manager = JobManager()

        # Try full authentication first
        full_token = None
        if credentials:
            full_token = credentials.credentials
        elif token:
            full_token = token

        if full_token:
            auth_result = auth_service.validate_token_full(full_token)
            if auth_result.is_valid:
                # For full auth, also verify job ownership
                job = job_manager.get_job(job_id)
                if not job:
                    raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

                # Check ownership: admin can access all, users only their own jobs
                if not auth_result.is_admin:
                    if auth_result.user_email and job.user_email:
                        if auth_result.user_email.lower() != job.user_email.lower():
                            logger.warning(
                                f"Review access denied: user {auth_result.user_email} "
                                f"tried to access job {job_id} owned by {job.user_email}"
                            )
                            raise HTTPException(
                                status_code=403,
                                detail="You don't have permission to access this job's review"
                            )
                    elif job.user_email:
                        # Token auth without email trying to access a job with owner
                        raise HTTPException(
                            status_code=403,
                            detail="You don't have permission to access this job's review"
                        )

                logger.info(f"Review access granted via full auth ({auth_result.user_type}) for job {job_id}")
                return job_id, "full"

        # Try review token
        if review_token:
            job = job_manager.get_job(job_id)

            if not job:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

            # Validate review token matches
            if job.review_token and secrets.compare_digest(job.review_token, review_token):
                # Check expiry if set
                if job.review_token_expires_at:
                    now = datetime.now(UTC)
                    # Handle timezone-naive datetimes from Firestore
                    expiry = job.review_token_expires_at
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=UTC)

                    if now > expiry:
                        logger.warning(f"Review token expired for job {job_id}")
                        raise HTTPException(
                            status_code=401,
                            detail="Review token has expired. Please request a new review link."
                        )

                logger.info(f"Review access granted via review_token for job {job_id}")
                return job_id, "review_token"
            else:
                logger.warning(f"Invalid review token for job {job_id}")

        # No valid authentication
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide either a full access token or a valid review_token for this job."
        )

    return require_review_auth


# Default instance for most review endpoints
require_review_auth = require_review_auth_factory()


