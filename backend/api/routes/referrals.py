"""
Referral API routes.

Handles:
- Public referral interstitial (click tracking)
- Authenticated referral dashboard
- Stripe Connect onboarding
- Admin vanity link creation and link management
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.api.dependencies import require_auth, require_admin
from backend.models.referral import (
    CreateReferralLinkRequest,
    UpdateReferralLinkRequest,
    ReferralInterstitialResponse,
)
from backend.services.flyer_service import get_flyer_service, FlyerError
from backend.services.referral_service import get_referral_service


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/referrals", tags=["referrals"])


class GenerateFlyerRequest(BaseModel):
    theme: str = Field(..., pattern="^(light|dark)$")
    qr_data_url: str = Field(..., max_length=500_000)


# ============================================================================
# Public endpoints
# ============================================================================


@router.get("/r/{code}", response_model=ReferralInterstitialResponse)
async def get_referral_interstitial(code: str):
    """
    Public endpoint: get interstitial data for a referral link.

    Increments click count. Returns valid=False if code not found.
    """
    service = get_referral_service()
    link = service.get_link_by_code(code)

    if not link:
        return ReferralInterstitialResponse(valid=False)

    # Track the click
    try:
        service.increment_clicks(code)
    except Exception:
        logger.warning("Failed to increment clicks for code %s", code, exc_info=True)

    return ReferralInterstitialResponse(
        valid=True,
        referral_code=link.code,
        referrer_display_name=link.display_name,
        discount_percent=link.discount_percent,
        discount_duration_days=link.discount_duration_days,
        custom_message=link.custom_message,
    )


# ============================================================================
# Authenticated user endpoints
# ============================================================================


@router.get("/me")
async def get_my_dashboard(auth=Depends(require_auth)):
    """Get referral dashboard data for the authenticated user."""
    service = get_referral_service()
    return service.get_dashboard_data(auth.user_email)


@router.put("/me")
async def update_my_link(
    updates: UpdateReferralLinkRequest,
    auth=Depends(require_auth),
):
    """Update display_name and custom_message on the user's own referral link."""
    service = get_referral_service()
    link = service.get_or_create_link(auth.user_email)

    update_fields = {}
    if updates.display_name is not None:
        update_fields["display_name"] = updates.display_name
    if updates.custom_message is not None:
        update_fields["custom_message"] = updates.custom_message

    if not update_fields:
        return {"ok": True, "message": "No changes"}

    success, message = service.update_link(link.code, **update_fields)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"ok": True, "message": message}


@router.post("/me/connect")
async def start_stripe_connect(auth=Depends(require_auth)):
    """Start Stripe Connect onboarding for the authenticated user."""
    # Check if user already has a Connect account
    from backend.services.user_service import get_user_service
    user_service = get_user_service()
    user = user_service.get_user(auth.user_email)
    if user and user.stripe_connect_account_id:
        raise HTTPException(status_code=400, detail="Stripe Connect account already configured")

    service = get_referral_service()
    account_id, onboarding_url = service.create_connect_account(auth.user_email)

    if not account_id:
        raise HTTPException(status_code=500, detail="Failed to create Stripe Connect account")

    user_service.update_user(auth.user_email, stripe_connect_account_id=account_id)

    return {"account_id": account_id, "onboarding_url": onboarding_url}


class VanityRequest(BaseModel):
    desired_code: str = Field(..., min_length=2, max_length=30, pattern="^[a-z0-9-]+$")


@router.post("/me/vanity-request")
async def request_vanity_url(
    request: VanityRequest,
    auth=Depends(require_auth),
):
    """Request a custom vanity referral URL. Sends email to admin for review."""
    from backend.services.email_service import get_email_service

    service = get_referral_service()
    link = service.get_or_create_link(auth.user_email)

    # Check if code is already taken
    existing = service.get_link_by_code(request.desired_code)
    if existing:
        raise HTTPException(status_code=409, detail="That code is already taken. Please try another.")

    email_service = get_email_service()
    email_service.provider.send_email(
        to_email="andrew@nomadkaraoke.com",
        subject=f"Vanity referral URL request: {request.desired_code}",
        html_content=f"""
        <h2>Vanity Referral URL Request</h2>
        <p><strong>User:</strong> {auth.user_email}</p>
        <p><strong>Current code:</strong> {link.code}</p>
        <p><strong>Desired vanity code:</strong> {request.desired_code}</p>
        <p>To approve, create the vanity link in the <a href="https://gen.nomadkaraoke.com/admin/referrals">admin dashboard</a>.</p>
        """,
        text_content=f"Vanity URL request from {auth.user_email}: {request.desired_code} (current: {link.code})",
    )

    logger.info("Vanity URL request from %s: %s", auth.user_email, request.desired_code)
    return {"ok": True, "message": "Request sent"}


@router.post("/me/flyer")
async def generate_flyer(
    request: GenerateFlyerRequest,
    auth=Depends(require_auth),
):
    """Generate a personalized referral flyer PDF."""
    service = get_referral_service()
    link = service.get_or_create_link(auth.user_email)

    try:
        flyer_service = get_flyer_service()
        pdf_bytes = flyer_service.generate_pdf(
            theme=request.theme,
            referral_code=link.code,
            discount_percent=link.discount_percent,
            qr_data_url=request.qr_data_url,
        )
    except FlyerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="nomad-karaoke-referral-flyer.pdf"',
        },
    )


# ============================================================================
# Admin endpoints
# ============================================================================


@router.post("/admin/vanity")
async def create_vanity_link(
    request: CreateReferralLinkRequest,
    auth=Depends(require_admin),
):
    """Admin: create a vanity referral link."""
    if not request.vanity_code:
        raise HTTPException(status_code=400, detail="vanity_code is required")

    service = get_referral_service()
    success, link, message = service.create_vanity_link(
        code=request.vanity_code,
        owner_email=request.owner_email,
        display_name=request.display_name,
        custom_message=request.custom_message,
        discount_percent=request.discount_percent,
        kickback_percent=request.kickback_percent,
        discount_duration_days=request.discount_duration_days,
        earning_duration_days=request.earning_duration_days,
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"ok": True, "code": link.code, "message": message}


@router.get("/admin/links")
async def list_links(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth=Depends(require_admin),
):
    """Admin: list all referral links."""
    service = get_referral_service()
    links = service.list_links(limit=limit, offset=offset)
    return {
        "links": [link.model_dump(mode="json") for link in links],
        "count": len(links),
    }


@router.post("/admin/apply-discount")
async def admin_apply_discount(
    request: Request,
    auth=Depends(require_admin),
):
    """Admin: apply a referral discount to any user, even without a referral link."""
    from backend.services.user_service import get_user_service

    body = await request.json()
    user_email = body.get("email", "").lower()
    discount_percent = body.get("discount_percent", 10)
    duration_days = body.get("duration_days", 30)
    referral_code = body.get("referral_code", "admin-grant")

    if not user_email:
        raise HTTPException(status_code=400, detail="Email is required")

    user_service = get_user_service()
    user = user_service.get_user(user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    from datetime import datetime, timedelta
    now = datetime.utcnow()

    user_service.update_user(
        user_email,
        referred_by_code=referral_code,
        referred_at=now,
        referral_discount_expires_at=now + timedelta(days=duration_days),
    )

    # Create/ensure the Stripe coupon exists for this discount percent
    service = get_referral_service()
    service.get_or_create_stripe_coupon(discount_percent)

    logger.info(f"Admin applied {discount_percent}% discount for {duration_days} days to {user_email}")

    return {
        "ok": True,
        "message": f"Applied {discount_percent}% discount for {duration_days} days",
        "discount_expires_at": (now + timedelta(days=duration_days)).isoformat(),
    }


@router.put("/admin/links/{code}")
async def update_link(
    code: str,
    updates: UpdateReferralLinkRequest,
    auth=Depends(require_admin),
):
    """Admin: update any referral link's settings."""
    service = get_referral_service()

    update_fields = {
        k: v for k, v in updates.model_dump().items() if v is not None
    }

    if not update_fields:
        return {"ok": True, "message": "No changes"}

    success, message = service.update_link(code, **update_fields)
    if not success:
        raise HTTPException(status_code=404, detail=message)

    return {"ok": True, "message": message}


@router.post("/admin/links/{code}/flyer")
async def admin_generate_flyer(
    code: str,
    request: GenerateFlyerRequest,
    auth=Depends(require_admin),
):
    """Admin: generate a personalized referral flyer PDF for any code."""
    service = get_referral_service()
    link = service.get_link_by_code(code)

    if not link:
        raise HTTPException(status_code=404, detail=f"Referral link '{code}' not found")

    try:
        flyer_service = get_flyer_service()
        pdf_bytes = flyer_service.generate_pdf(
            theme=request.theme,
            referral_code=link.code,
            discount_percent=link.discount_percent,
            qr_data_url=request.qr_data_url,
        )
    except FlyerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="nomad-karaoke-flyer-{link.code}.pdf"',
        },
    )
