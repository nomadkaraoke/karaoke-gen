"""
Push Notification API routes.

Provides endpoints for managing Web Push notification subscriptions:
- GET /api/push/vapid-public-key: Get VAPID public key for client-side subscription
- POST /api/push/subscribe: Register a push subscription
- POST /api/push/unsubscribe: Remove a push subscription
- GET /api/push/subscriptions: List user's subscriptions
- POST /api/push/test: Send a test notification (admin only)
"""
import logging
from typing import Optional, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.api.dependencies import require_auth, require_admin
from backend.services.auth_service import AuthResult
from backend.services.push_notification_service import get_push_notification_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/push", tags=["push"])


# Request/Response Models

class VapidPublicKeyResponse(BaseModel):
    """Response containing VAPID public key."""
    enabled: bool
    vapid_public_key: Optional[str] = None


class SubscribeRequest(BaseModel):
    """Request to subscribe to push notifications."""
    endpoint: str
    keys: Dict[str, str]  # p256dh and auth
    device_name: Optional[str] = None


class SubscribeResponse(BaseModel):
    """Response after subscribing."""
    status: str
    message: str


class UnsubscribeRequest(BaseModel):
    """Request to unsubscribe from push notifications."""
    endpoint: str


class UnsubscribeResponse(BaseModel):
    """Response after unsubscribing."""
    status: str
    message: str


class SubscriptionInfo(BaseModel):
    """Information about a push subscription."""
    endpoint: str
    device_name: Optional[str] = None
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None


class SubscriptionsListResponse(BaseModel):
    """Response containing user's subscriptions."""
    subscriptions: List[SubscriptionInfo]
    count: int


class TestNotificationRequest(BaseModel):
    """Request to send a test notification."""
    title: Optional[str] = "Test Notification"
    body: Optional[str] = "This is a test push notification from Karaoke Generator"


class TestNotificationResponse(BaseModel):
    """Response after sending test notification."""
    status: str
    sent_count: int
    message: str


# Routes

@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
async def get_vapid_public_key():
    """
    Get the VAPID public key for push subscription.

    This endpoint is public - no authentication required.
    Returns the public key needed for client-side PushManager.subscribe().
    """
    settings = get_settings()
    push_service = get_push_notification_service()

    if not settings.enable_push_notifications:
        return VapidPublicKeyResponse(enabled=False)

    public_key = push_service.get_public_key()
    if not public_key:
        return VapidPublicKeyResponse(enabled=False)

    return VapidPublicKeyResponse(
        enabled=True,
        vapid_public_key=public_key
    )


@router.post("/subscribe", response_model=SubscribeResponse)
async def subscribe_push(
    request: SubscribeRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Register a push notification subscription for the current user.

    Requires authentication. Users can have up to 5 subscriptions
    (configurable via MAX_PUSH_SUBSCRIPTIONS_PER_USER).
    """
    settings = get_settings()
    if not settings.enable_push_notifications:
        raise HTTPException(status_code=503, detail="Push notifications are not enabled")

    if not auth_result.user_email:
        raise HTTPException(status_code=401, detail="User email not available")

    push_service = get_push_notification_service()

    # Validate keys
    if "p256dh" not in request.keys or "auth" not in request.keys:
        raise HTTPException(status_code=400, detail="Missing required keys (p256dh, auth)")

    success = await push_service.add_subscription(
        user_email=auth_result.user_email,
        endpoint=request.endpoint,
        keys=request.keys,
        device_name=request.device_name
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to save subscription")

    return SubscribeResponse(
        status="success",
        message="Push subscription registered successfully"
    )


@router.post("/unsubscribe", response_model=UnsubscribeResponse)
async def unsubscribe_push(
    request: UnsubscribeRequest,
    auth_result: AuthResult = Depends(require_auth)
):
    """
    Remove a push notification subscription.

    Requires authentication. Users can only remove their own subscriptions.
    """
    if not auth_result.user_email:
        raise HTTPException(status_code=401, detail="User email not available")

    push_service = get_push_notification_service()

    success = await push_service.remove_subscription(
        user_email=auth_result.user_email,
        endpoint=request.endpoint
    )

    if not success:
        # Don't error if subscription wasn't found - might already be removed
        return UnsubscribeResponse(
            status="success",
            message="Subscription removed (or was not found)"
        )

    return UnsubscribeResponse(
        status="success",
        message="Push subscription removed successfully"
    )


@router.get("/subscriptions", response_model=SubscriptionsListResponse)
async def list_subscriptions(
    auth_result: AuthResult = Depends(require_auth)
):
    """
    List all push notification subscriptions for the current user.

    Requires authentication.
    """
    if not auth_result.user_email:
        raise HTTPException(status_code=401, detail="User email not available")

    push_service = get_push_notification_service()

    subscriptions = await push_service.list_subscriptions(auth_result.user_email)

    return SubscriptionsListResponse(
        subscriptions=[SubscriptionInfo(**s) for s in subscriptions],
        count=len(subscriptions)
    )


@router.post("/test", response_model=TestNotificationResponse)
async def send_test_notification(
    request: TestNotificationRequest,
    auth_result: AuthResult = Depends(require_admin)
):
    """
    Send a test push notification to the current user's devices.

    Admin only. Useful for testing push notification setup.
    """
    settings = get_settings()
    if not settings.enable_push_notifications:
        raise HTTPException(status_code=503, detail="Push notifications are not enabled")

    if not auth_result.user_email:
        raise HTTPException(status_code=401, detail="User email not available")

    push_service = get_push_notification_service()

    sent_count = await push_service.send_push(
        user_email=auth_result.user_email,
        title=request.title or "Test Notification",
        body=request.body or "This is a test push notification",
        url="/app/",
        tag="test"
    )

    return TestNotificationResponse(
        status="success",
        sent_count=sent_count,
        message=f"Test notification sent to {sent_count} device(s)"
    )
