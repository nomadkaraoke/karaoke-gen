"""kjbox partner API — singer email-code sign-in + "free at the show" credits.

kjbox (the KJ device app at live karaoke nights) lets a patron verify their
email inside the kjbox singer web page, then creates gen jobs AS that real gen
user (with the returned session token) so they get normal gen delivery emails.

Every endpoint requires ``X-Kjbox-Secret`` (503 until ``KJBOX_PARTNER_SECRET``
is configured — deploys dark; 403 if wrong). Contract:

- POST /api/kjbox/auth/send-code    {email, locale?, venue?} → {"status": "sent"}
- POST /api/kjbox/auth/verify-code  {email, code} → {session_token, user,
                                     credits_granted, credit_status}
- POST /api/kjbox/credits/show-credit (+ Authorization: Bearer <session>)
                                     {idempotency_key, venue?} → {granted, credits}

A venue is many singers behind one IP, so gen's 2-signups-per-IP cap does NOT
apply here; instead a partner-wide rolling-24h cap on new accounts, a per-email
code throttle, and a per-user daily show-credit cap bound abuse.
"""
from __future__ import annotations

import hmac
import logging
import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.routes.users import (
    _build_user_public,
    _mask_email,
    _precompute_credit_eval,
    complete_verified_login,
)
from backend.config import get_settings, is_production
from backend.i18n import SUPPORTED_LOCALES, t
from backend.services.email_service import EmailService, get_email_service
from backend.services.email_validation_service import get_email_validation_service
from backend.services.kjbox_partner_service import (
    CODE_EXPIRY_MINUTES,
    LOGIN_CODES_COLLECTION,
    SHOW_CREDIT_REASON,
    KjboxPartnerService,
    VerifyOutcome,
)
from backend.services.user_service import UserService, get_user_service
from backend.utils.request_helpers import get_client_ip
from backend.utils.test_data import is_test_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kjbox", tags=["kjbox"])

SIGNUP_SOURCE = "kjbox"
_MAX_EMAIL_LEN = 254
_MAX_VENUE_LEN = 200


# ------------------------------------------------------------------ auth gate

def require_kjbox_partner(x_kjbox_secret: Optional[str] = Header(default=None)) -> str:
    """Shared-secret gate. Returns the secret (also used as the code-hash pepper)."""
    secret = get_settings().kjbox_partner_secret
    if not secret:
        raise HTTPException(status_code=503, detail="not configured")
    if not hmac.compare_digest((x_kjbox_secret or "").encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="forbidden")
    return secret


def get_kjbox_service(
    secret: str = Depends(require_kjbox_partner),
    user_service: UserService = Depends(get_user_service),
) -> KjboxPartnerService:
    return KjboxPartnerService(user_service.db, pepper=secret)


# --------------------------------------------------------------------- models

class SendCodeRequest(BaseModel):
    email: str
    locale: Optional[str] = None
    venue: Optional[str] = Field(default=None, max_length=_MAX_VENUE_LEN)


class SendCodeResponse(BaseModel):
    status: str


class VerifyCodeRequest(BaseModel):
    email: str
    code: str = Field(max_length=32)


class VerifyCodeResponse(BaseModel):
    session_token: str
    user: dict
    credits_granted: int
    credit_status: str


class ShowCreditRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    venue: Optional[str] = Field(default=None, max_length=_MAX_VENUE_LEN)


class ShowCreditResponse(BaseModel):
    granted: bool
    credits: int


# -------------------------------------------------------------------- helpers

def _normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    local, _, domain = email.partition("@")
    if (not local or "." not in domain or len(email) > _MAX_EMAIL_LEN
            or any(c.isspace() for c in email) or email.count("@") != 1):
        raise HTTPException(status_code=422, detail="invalid_email")
    return email


def _split_locale(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (email_locale in en/es/de or None, ui_locale primary subtag or None)."""
    if not raw:
        return None, None
    primary = raw.strip().replace("_", "-").split("-")[0].lower()
    if not primary.isalpha() or len(primary) < 2:
        return None, None
    return (primary if primary in SUPPORTED_LOCALES else None), primary


def _pretend_sent() -> SendCodeResponse:
    return SendCodeResponse(status="sent")


# ------------------------------------------------------------------ endpoints

@router.post("/auth/send-code", response_model=SendCodeResponse)
async def send_code(
    body: SendCodeRequest,
    http_request: Request,
    svc: KjboxPartnerService = Depends(get_kjbox_service),
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    settings = get_settings()
    email = _normalize_email(body.email)
    email_locale, ui_locale = _split_locale(body.locale)
    locale = email_locale or "en"
    venue = (body.venue or "").strip() or None

    email_validation = get_email_validation_service()
    if email_validation.is_disposable_domain(email):
        logger.warning(f"kjbox: blocked disposable email {_mask_email(email)}")
        raise HTTPException(status_code=422, detail=t(locale, "users.disposableEmailBlocked"))
    if email_validation.is_email_blocked(email):
        logger.warning(f"kjbox: blocked email {_mask_email(email)} — pretending success")
        return _pretend_sent()
    ip_address = get_client_ip(http_request)
    if ip_address and email_validation.is_ip_blocked(ip_address):
        logger.warning(f"kjbox: blocked IP {ip_address} — pretending success")
        return _pretend_sent()

    if svc.is_email_throttled(email, settings.kjbox_codes_per_email_per_hour):
        raise HTTPException(status_code=429, detail="too_many_codes")

    if not email_service.is_configured() and is_production():
        logger.error("kjbox: email service not configured - cannot send codes")
        raise HTTPException(status_code=503, detail="email not configured")

    user = user_service.get_user(email)
    if user is None:
        if svc.count_recent_signups() >= settings.kjbox_signup_cap_per_24h:
            logger.warning("kjbox: partner-wide signup cap reached")
            raise HTTPException(status_code=429, detail="signup_cap")
        # No signup_ip: the caller is the kjbox box, whose IP is the whole
        # venue's — recording it would trip gen's per-IP cap for everyone there.
        user = user_service.get_or_create_user(
            email, signup_source=SIGNUP_SOURCE, signup_venue=venue,
        )
        svc.record_signup(email, venue)
        logger.info(f"kjbox: created gen user {_mask_email(email)} (venue={venue!r})")

    issued = svc.issue_code(
        email,
        needs_credit_eval=not getattr(user, "welcome_credits_granted", False) and not is_test_email(email),
        locale=email_locale,
        ui_locale=ui_locale,
        venue=venue,
        ip_address=ip_address,
        user_agent=http_request.headers.get("user-agent"),
    )

    sent = email_service.send_kjbox_login_code(
        email, issued.code, expiry_minutes=CODE_EXPIRY_MINUTES, locale=locale,
    )
    if not sent:
        if getattr(email_service, "last_send_suppressed", False):
            logger.info(f"kjbox: code recipient {_mask_email(email)} is Postmark-suppressed")
        else:
            logger.error(f"kjbox: failed to send code email to {_mask_email(email)}")

    # Warm the welcome-credit AI evaluation while the singer reads their email,
    # so verify-code is instant (same optimisation as magic links).
    if issued.needs_credit_eval:
        threading.Thread(
            target=_precompute_credit_eval,
            args=(issued.doc_id, email, LOGIN_CODES_COLLECTION),
            daemon=True,
        ).start()

    return SendCodeResponse(status="sent")


@router.post("/auth/verify-code", response_model=VerifyCodeResponse)
async def verify_code(
    body: VerifyCodeRequest,
    http_request: Request,
    svc: KjboxPartnerService = Depends(get_kjbox_service),
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    email = _normalize_email(body.email)

    outcome, record = svc.verify_code(email, body.code)
    if outcome == VerifyOutcome.TOO_MANY_ATTEMPTS:
        raise HTTPException(status_code=429, detail="too_many_attempts")
    if outcome == VerifyOutcome.INVALID:
        raise HTTPException(status_code=401, detail="invalid_code")
    if outcome != VerifyOutcome.OK or record is None:
        raise HTTPException(status_code=401, detail="expired")

    user = user_service.get_user(email) or user_service.get_or_create_user(
        email, signup_source=SIGNUP_SOURCE, signup_venue=record.get("venue"),
    )
    # Same first-login definition as magic-link verify (checked before last_login_at is set).
    is_first_login = user.total_jobs_created == 0 and not user.last_login_at
    user = user_service.update_user(
        email, email_verified=True, last_login_at=datetime.utcnow(),
    ) or user

    precomputed_eval = {
        k: record.get(k)
        for k in ("credit_eval_decision", "credit_eval_reasoning", "credit_eval_error")
        if record.get(k) is not None
    } or None
    email_locale = record.get("locale")

    result = complete_verified_login(
        user,
        user_service=user_service,
        email_service=email_service,
        is_first_login=is_first_login,
        precomputed_eval=precomputed_eval,
        grant_welcome_credit=True,
        referral_code=None,
        locale=email_locale,
        ui_locale=record.get("ui_locale"),
        ip_address=get_client_ip(http_request),
        user_agent=http_request.headers.get("user-agent"),
        tenant_id=None,
        device_fingerprint=None,
        email_locale=email_locale or user.locale or "en",
    )
    logger.info(f"kjbox: verified {_mask_email(email)} (credit_status={result.credit_status})")

    return VerifyCodeResponse(
        session_token=result.session.token,
        user=_build_user_public(result.user).model_dump(mode="json"),
        credits_granted=result.credits_granted,
        credit_status=result.credit_status,
    )


@router.post("/credits/show-credit", response_model=ShowCreditResponse)
async def show_credit(
    body: ShowCreditRequest,
    svc: KjboxPartnerService = Depends(get_kjbox_service),
    user_service: UserService = Depends(get_user_service),
    authorization: Optional[str] = Header(default=None),
):
    """Quietly grant +1 credit so a singer's make-it job is free at the show.

    No "credits added" email. Idempotent per ``idempotency_key``; capped per user
    per rolling 24h.
    """
    settings = get_settings()
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid_session")
    valid, user, _msg = user_service.validate_session(token)
    if not valid or not user:
        raise HTTPException(status_code=401, detail="invalid_session")

    email = user.email.lower()
    venue = (body.venue or "").strip() or None

    if svc.show_credit_already_processed(body.idempotency_key):
        return ShowCreditResponse(granted=False, credits=user.credits)

    if svc.count_recent_show_credits(email) >= settings.kjbox_show_credits_per_user_per_24h:
        raise HTTPException(status_code=429, detail="show_credit_cap")

    if not svc.claim_show_credit(body.idempotency_key, email, venue):
        current = user_service.get_user(email)
        return ShowCreditResponse(granted=False, credits=current.credits if current else user.credits)

    ok, new_balance, message = user_service.add_credits(email, 1, SHOW_CREDIT_REASON)
    if not ok:
        svc.release_show_credit(body.idempotency_key)
        logger.error(f"kjbox: show credit grant failed for {_mask_email(email)}: {message}")
        raise HTTPException(status_code=500, detail="grant_failed")

    logger.info(f"kjbox: show credit granted to {_mask_email(email)} (venue={venue!r}) → {new_balance}")
    return ShowCreditResponse(granted=True, credits=new_balance)
