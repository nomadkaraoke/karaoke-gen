"""
Authentication service for karaoke backend.

Provides token-based authentication with support for:
- Admin tokens (hardcoded in environment)
- Admin by email domain (@nomadkaraoke.com)
- User tokens (stored in Firestore auth_tokens collection)
- Session tokens (stored in Firestore sessions collection, from magic link auth)
- API keys for business users
- Usage tracking and limits
- Token management API
"""
import logging
import hashlib
import time
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime

from backend.services.firestore_service import FirestoreService
from backend.config import get_settings
from backend.models.user import UserRole


logger = logging.getLogger(__name__)

# Admin email domains - users with these domains are automatically admin
ADMIN_EMAIL_DOMAINS = ["nomadkaraoke.com"]


class UserType(str, Enum):
    """User access levels."""
    ADMIN = "admin"          # Unlimited access, can manage tokens
    UNLIMITED = "unlimited"  # Unlimited karaoke generation
    LIMITED = "limited"      # Limited number of uses
    STRIPE = "stripe"        # Paid access via Stripe
    API_KEY = "api_key"      # Business API key access


@dataclass
class AuthResult:
    """Result of authentication validation."""
    is_valid: bool
    user_type: UserType
    remaining_uses: int  # -1 = unlimited, 0 = exhausted, >0 = remaining
    message: str
    user_email: Optional[str] = None  # Email if authenticated via session/API key
    is_admin: bool = False  # True if admin token or admin email domain
    api_key_id: Optional[str] = None  # API key ID if authenticated via API key
    tenant_id: Optional[str] = None  # Tenant scope from token or session

    def __iter__(self):
        """Allow unpacking as tuple for backward compatibility."""
        return iter((self.is_valid, self.user_type, self.remaining_uses, self.message))


def is_admin_email(email: str) -> bool:
    """Check if an email belongs to an admin domain."""
    if not email:
        return False
    email_lower = email.lower()
    return any(email_lower.endswith(f"@{domain}") for domain in ADMIN_EMAIL_DOMAINS)


class AuthService:
    """Service for authentication and token management."""

    def __init__(self):
        """Initialize auth service."""
        self.firestore = FirestoreService()
        self.settings = get_settings()
        self._init_admin_tokens()

    def _init_admin_tokens(self):
        """Load admin tokens from environment variables."""
        # Get admin tokens from secret manager or env vars
        admin_tokens_str = self.settings.admin_tokens or ""
        self.admin_tokens = [t.strip() for t in admin_tokens_str.split(",") if t.strip()]

        if self.admin_tokens:
            logger.info(f"Loaded {len(self.admin_tokens)} admin token(s)")
        else:
            logger.warning("No admin tokens configured! Set ADMIN_TOKENS environment variable.")
    
    def validate_token(self, token: str) -> Tuple[bool, UserType, int, str]:
        """
        Validate an access token.
        
        Returns:
            (is_valid, user_type, remaining_uses, message)
            remaining_uses: -1 = unlimited, 0 = exhausted, >0 = remaining count
        """
        if not token:
            return False, UserType.LIMITED, 0, "No token provided"
        
        # Check for admin tokens (highest priority)
        if token in self.admin_tokens:
            logger.info("Admin token validated")
            return True, UserType.ADMIN, -1, "Admin access granted"
        
        # Debug: Log token comparison info (only first/last few chars for security)
        if self.admin_tokens:
            expected_prefix = self.admin_tokens[0][:8] if len(self.admin_tokens[0]) >= 8 else self.admin_tokens[0]
            provided_prefix = token[:8] if len(token) >= 8 else token
            logger.debug(
                f"Token mismatch: expected prefix '{expected_prefix}...', "
                f"got '{provided_prefix}...', "
                f"expected len={len(self.admin_tokens[0])}, got len={len(token)}"
            )
        
        # Check stored tokens in Firestore (auth_tokens collection)
        token_data = self.firestore.get_token(token)

        if not token_data:
            # Fall back to checking session tokens (from magic link auth)
            # Import here to avoid circular dependency
            from backend.services.user_service import get_user_service

            user_service = get_user_service()
            is_valid, user, message = user_service.validate_session(token)

            if is_valid and user:
                # Session is valid - user is authenticated via magic link
                # Return their credits as remaining uses
                credits = user.credits
                logger.info(f"Session token validated for user {user.email} (credits: {credits})")

                if credits <= 0:
                    # User has no credits but is authenticated
                    return True, UserType.STRIPE, 0, f"Authenticated but no credits remaining"

                return True, UserType.STRIPE, credits, f"Session valid: {credits} credits"

            # Neither auth_token nor session found
            return False, UserType.LIMITED, 0, "Invalid token"
        
        # Check if token is active
        if not token_data.get("active", True):
            return False, UserType(token_data["type"]), 0, "Token has been revoked"
        
        token_type = UserType(token_data["type"])
        max_uses = token_data.get("max_uses", -1)
        
        # UNLIMITED tokens: no usage limits
        if token_type == UserType.UNLIMITED:
            return True, token_type, -1, "Unlimited access granted"
        
        # LIMITED tokens: check usage count
        if token_type == UserType.LIMITED:
            if max_uses <= 0:  # -1 means unlimited
                return True, token_type, -1, "Limited token with unlimited uses"
            
            current_uses = token_data.get("usage_count", 0)
            remaining = max_uses - current_uses
            
            if remaining <= 0:
                return False, token_type, 0, "Token usage limit exceeded"
            
            return True, token_type, remaining, f"Limited token: {remaining} uses remaining"
        
        # STRIPE tokens: check expiration and usage
        if token_type == UserType.STRIPE:
            expires_at = token_data.get("expires_at")
            
            if expires_at:
                # expires_at is stored as timestamp
                if time.time() > expires_at:
                    return False, token_type, 0, "Token has expired"
            
            if max_uses > 0:
                current_uses = token_data.get("usage_count", 0)
                remaining = max_uses - current_uses
                
                if remaining <= 0:
                    return False, token_type, 0, "Token usage limit exceeded"
                
                return True, token_type, remaining, f"Stripe token: {remaining} uses remaining"
            
            return True, token_type, -1, "Stripe access granted"
        
        return False, UserType.LIMITED, 0, "Unknown token type"

    def validate_token_full(self, token: str) -> AuthResult:
        """
        Validate an access token and return full authentication details.

        This is the preferred method for new code as it returns complete
        authentication context including user email and admin status.

        Returns:
            AuthResult with full authentication details
        """
        if not token:
            return AuthResult(
                is_valid=False,
                user_type=UserType.LIMITED,
                remaining_uses=0,
                message="No token provided"
            )

        # Check for admin tokens (highest priority)
        # Admin tokens from env var are associated with the system admin email
        if token in self.admin_tokens:
            logger.info("Admin token validated")
            return AuthResult(
                is_valid=True,
                user_type=UserType.ADMIN,
                remaining_uses=-1,
                message="Admin access granted",
                user_email="admin@nomadkaraoke.com",
                is_admin=True
            )

        # Check stored tokens in Firestore (auth_tokens collection)
        token_data = self.firestore.get_token(token)

        if not token_data:
            # Fall back to checking session tokens (from magic link auth)
            from backend.services.user_service import get_user_service

            user_service = get_user_service()
            is_valid, user, _message = user_service.validate_session(token)

            if is_valid and user:
                # Session is valid - user is authenticated via magic link
                credits = user.credits
                user_email = user.email

                # Check if user is admin by email domain or role
                user_is_admin = is_admin_email(user_email) or user.role == UserRole.ADMIN

                if user_is_admin:
                    logger.info(f"Admin session validated for {user_email}")
                    return AuthResult(
                        is_valid=True,
                        user_type=UserType.ADMIN,
                        remaining_uses=-1,
                        message="Admin access granted (domain)",
                        user_email=user_email,
                        is_admin=True
                    )

                logger.info(f"Session token validated for user {user_email} (credits: {credits})")

                if credits <= 0:
                    return AuthResult(
                        is_valid=True,
                        user_type=UserType.STRIPE,
                        remaining_uses=0,
                        message="Authenticated but no credits remaining",
                        user_email=user_email,
                        is_admin=False
                    )

                return AuthResult(
                    is_valid=True,
                    user_type=UserType.STRIPE,
                    remaining_uses=credits,
                    message=f"Session valid: {credits} credits",
                    user_email=user_email,
                    is_admin=False
                )

            # Neither auth_token nor session found
            return AuthResult(
                is_valid=False,
                user_type=UserType.LIMITED,
                remaining_uses=0,
                message="Invalid token"
            )

        # Check if token is active
        if not token_data.get("active", True):
            return AuthResult(
                is_valid=False,
                user_type=UserType(token_data["type"]),
                remaining_uses=0,
                message="Token has been revoked"
            )

        token_type = UserType(token_data["type"])
        max_uses = token_data.get("max_uses", -1)
        # All auth_tokens must have an associated user_email for job ownership
        token_user_email = token_data.get("user_email")
        api_key_id = token_data.get("api_key_id")
        token_tenant_id = token_data.get("tenant_id")

        # Require user_email on all auth_tokens (no anonymous token auth)
        if not token_user_email:
            logger.warning("Auth token missing required user_email field")
            return AuthResult(
                is_valid=False,
                user_type=token_type,
                remaining_uses=0,
                message="Token configuration error: missing user_email. Please contact support."
            )

        # Check if token's user is an admin (by email domain)
        token_is_admin = is_admin_email(token_user_email)

        # UNLIMITED tokens: no usage limits
        if token_type == UserType.UNLIMITED:
            return AuthResult(
                is_valid=True,
                user_type=token_type,
                remaining_uses=-1,
                message="Unlimited access granted",
                user_email=token_user_email,
                is_admin=token_is_admin,
                api_key_id=api_key_id,
                tenant_id=token_tenant_id
            )

        # LIMITED tokens: check usage count
        if token_type == UserType.LIMITED:
            if max_uses <= 0:  # -1 means unlimited
                return AuthResult(
                    is_valid=True,
                    user_type=token_type,
                    remaining_uses=-1,
                    message="Limited token with unlimited uses",
                    user_email=token_user_email,
                    is_admin=token_is_admin,
                    api_key_id=api_key_id,
                    tenant_id=token_tenant_id
                )

            current_uses = token_data.get("usage_count", 0)
            remaining = max_uses - current_uses

            if remaining <= 0:
                return AuthResult(
                    is_valid=False,
                    user_type=token_type,
                    remaining_uses=0,
                    message="Token usage limit exceeded",
                    user_email=token_user_email,
                    api_key_id=api_key_id,
                    tenant_id=token_tenant_id
                )

            return AuthResult(
                is_valid=True,
                user_type=token_type,
                remaining_uses=remaining,
                message=f"Limited token: {remaining} uses remaining",
                user_email=token_user_email,
                is_admin=token_is_admin,
                api_key_id=api_key_id,
                tenant_id=token_tenant_id
            )

        # STRIPE tokens: check expiration and usage
        if token_type == UserType.STRIPE:
            expires_at = token_data.get("expires_at")

            if expires_at and time.time() > expires_at:
                return AuthResult(
                    is_valid=False,
                    user_type=token_type,
                    remaining_uses=0,
                    message="Token has expired",
                    user_email=token_user_email,
                    api_key_id=api_key_id,
                    tenant_id=token_tenant_id
                )

            if max_uses > 0:
                current_uses = token_data.get("usage_count", 0)
                remaining = max_uses - current_uses

                if remaining <= 0:
                    return AuthResult(
                        is_valid=False,
                        user_type=token_type,
                        remaining_uses=0,
                        message="Token usage limit exceeded",
                        user_email=token_user_email,
                        api_key_id=api_key_id,
                        tenant_id=token_tenant_id
                    )

                return AuthResult(
                    is_valid=True,
                    user_type=token_type,
                    remaining_uses=remaining,
                    message=f"Stripe token: {remaining} uses remaining",
                    user_email=token_user_email,
                    is_admin=token_is_admin,
                    api_key_id=api_key_id,
                    tenant_id=token_tenant_id
                )

            return AuthResult(
                is_valid=True,
                user_type=token_type,
                remaining_uses=-1,
                message="Stripe access granted",
                user_email=token_user_email,
                is_admin=token_is_admin,
                api_key_id=api_key_id,
                tenant_id=token_tenant_id
            )

        # API_KEY tokens
        if token_type == UserType.API_KEY:
            if max_uses > 0:
                current_uses = token_data.get("usage_count", 0)
                remaining = max_uses - current_uses
                if remaining <= 0:
                    return AuthResult(
                        is_valid=False,
                        user_type=token_type,
                        remaining_uses=0,
                        message="API key usage limit exceeded",
                        user_email=token_user_email,
                        api_key_id=api_key_id,
                        tenant_id=token_tenant_id
                    )
                return AuthResult(
                    is_valid=True,
                    user_type=token_type,
                    remaining_uses=remaining,
                    message=f"API key valid: {remaining} uses remaining",
                    user_email=token_user_email,
                    is_admin=token_is_admin,
                    api_key_id=api_key_id,
                    tenant_id=token_tenant_id
                )

            return AuthResult(
                is_valid=True,
                user_type=token_type,
                remaining_uses=-1,
                message="API key access granted",
                user_email=token_user_email,
                is_admin=token_is_admin,
                api_key_id=api_key_id,
                tenant_id=token_tenant_id
            )

        return AuthResult(
            is_valid=False,
            user_type=UserType.LIMITED,
            remaining_uses=0,
            message="Unknown token type"
        )

    def increment_token_usage(self, token: str, job_id: str) -> bool:
        """
        Increment usage count for an auth_token and track the job.

        Note: Credit deduction for session-based users is handled in
        JobManager.create_job(), not here. This method only tracks usage
        for legacy auth_tokens stored in Firestore.

        Args:
            token: The access token
            job_id: The job ID being created

        Returns:
            True if usage was tracked, False otherwise
        """
        # Validate token first
        is_valid, user_type, remaining_uses, message = self.validate_token(token)

        if not is_valid:
            logger.warning(f"Cannot increment usage for invalid token: {message}")
            return False

        # Don't track usage for admin or unlimited tokens
        if user_type in [UserType.ADMIN, UserType.UNLIMITED]:
            logger.debug(f"Skipping usage tracking for {user_type} token")
            return True

        # For admin tokens (not in Firestore), no tracking needed
        if token in self.admin_tokens:
            return True

        # Only increment usage for auth_tokens in Firestore
        # Session tokens (magic link auth) have credits managed by JobManager.create_job()
        token_data = self.firestore.get_token(token)
        if not token_data:
            return True

        # Regular auth_token - increment usage in Firestore
        try:
            self.firestore.increment_token_usage(token, job_id)
            logger.info(f"Incremented usage for token (remaining: {remaining_uses - 1})")
            return True
        except Exception as e:
            logger.error(f"Failed to increment token usage: {e}")
            return False
    
    def create_token(
        self,
        token_value: str,
        token_type: UserType,
        max_uses: int = -1,
        expires_at: Optional[float] = None,
        created_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new access token.
        
        Args:
            token_value: The actual token string (should be secure random string)
            token_type: Type of access (admin, unlimited, limited, stripe)
            max_uses: Maximum number of uses (-1 = unlimited)
            expires_at: Expiration timestamp (None = no expiration)
            created_by: Admin token that created this token
            
        Returns:
            Token data dictionary
        """
        token_data = {
            "token": token_value,
            "type": token_type.value,
            "max_uses": max_uses,
            "usage_count": 0,
            "active": True,
            "created_at": time.time(),
            "created_by": created_by,
            "expires_at": expires_at,
            "jobs": []
        }
        
        self.firestore.create_token(token_value, token_data)
        logger.info(f"Created new {token_type} token with max_uses={max_uses}")
        
        return token_data
    
    def revoke_token(self, token: str, revoked_by: Optional[str] = None) -> bool:
        """
        Revoke an access token.
        
        Args:
            token: The token to revoke
            revoked_by: Admin token that revoked this token
            
        Returns:
            True if revoked, False if token not found
        """
        try:
            self.firestore.update_token(token, {
                "active": False,
                "revoked_at": time.time(),
                "revoked_by": revoked_by
            })
            logger.info(f"Revoked token")
            return True
        except Exception as e:
            logger.error(f"Failed to revoke token: {e}")
            return False
    
    def list_tokens(self, include_inactive: bool = False) -> list:
        """
        List all tokens (admin only).
        
        Args:
            include_inactive: Whether to include revoked tokens
            
        Returns:
            List of token data dictionaries (with sensitive data masked)
        """
        try:
            tokens = self.firestore.list_tokens()
            
            # Filter inactive if requested
            if not include_inactive:
                tokens = [t for t in tokens if t.get("active", True)]
            
            # Mask token values for security (show only first 8 chars)
            for token in tokens:
                if "token" in token and len(token["token"]) > 8:
                    token["token"] = token["token"][:8] + "..."
            
            return tokens
        except Exception as e:
            logger.error(f"Failed to list tokens: {e}")
            return []
    
    def get_token_info(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a token (for admin dashboard).
        
        Returns:
            Token data or None if not found
        """
        # Check if it's an admin token (not stored in Firestore)
        if token in self.admin_tokens:
            return {
                "token": token[:8] + "...",
                "type": UserType.ADMIN.value,
                "max_uses": -1,
                "usage_count": 0,
                "active": True,
                "source": "environment"
            }
        
        # Get from Firestore
        token_data = self.firestore.get_token(token)
        
        if token_data and "token" in token_data:
            # Mask the full token value
            token_data["token"] = token_data["token"][:8] + "..."
        
        return token_data


# Global instance
_auth_service = None


def get_auth_service() -> AuthService:
    """Get the global auth service instance."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service

