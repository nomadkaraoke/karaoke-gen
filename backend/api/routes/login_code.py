"""gen email-code sign-in: ``POST /api/users/auth/send-code`` + ``verify-code``.

An alternative to the magic link for clients that can't follow an emailed link
back into the same session (the mobile app, a second device, local E2E). The
email carries BOTH a 6-digit code and a regular magic link; either signs in.

- POST /api/users/auth/send-code   {email, locale?, device_fingerprint?,
                                    referral_code?, attribution?} → 202 {"status": "sent"}
- POST /api/users/auth/verify-code {email, code} → VerifyMagicLinkResponse
- POST /api/users/auth/handoff-token {target, job_id, locale?} (Bearer)
                                   → {url, expires_in_seconds}: the mobile app's
                                     2-minute, single-use signed-in web link

Code mechanics come from :class:`LoginCodeStore` (shared with kjbox): HMAC-hashed
at rest with a server pepper, 10-minute TTL, 5 verify attempts then 429,
single-use marker, and per-email + per-IP rolling-hour send throttles checked in
the same Firestore transaction as the code write.

Abuse checks mirror ``POST /auth/magic-link``: disposable domain → 422 (unless a
tenant explicitly allowlisted the address), blocked email / IP → pretend
success, new-user per-IP/fingerprint signup cap → pretend success, tenant
email-domain restriction → pretend success, no email provider in production →
503. Pretend-success responses are indistinguishable from a real send.

Account creation: the email's magic link is minted with the normal
``UserService.create_magic_link`` machinery, which (exactly like the magic-link
endpoint) creates the account at send time, recording signup IP, device
fingerprint and first-touch attribution. That keeps magic-link parity (same
signup caps, same audit fields) instead of inventing a lazily-minted link.
verify-code still re-creates the account from the code record if it's gone.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime
from typing import Annotated, Dict, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from backend.api.routes.kjbox import _normalize_email, _split_locale, get_owned_review_job
from backend.api.routes.users import (
    _build_user_public,
    _mask_email,
    _precompute_credit_eval,
    complete_verified_login,
)
from backend.config import get_settings, is_production
from backend.i18n import SUPPORTED_LOCALES, get_full_locale_from_request, get_locale_from_request, t
from backend.models.user import VerifyMagicLinkResponse
from backend.services.email_service import EmailService, get_email_service
from backend.services.email_validation_service import get_email_validation_service
from backend.services.login_code_store import (
    CODE_EXPIRY_MINUTES,
    CodeThrottled,
    LoginCodeStore,
    TransactionContention,
    VerifyOutcome,
)
from backend.services.user_service import MAGIC_LINKS_COLLECTION, UserService, get_user_service
from backend.utils.request_helpers import get_client_ip
from backend.utils.test_data import is_test_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])

LOGIN_CODES_COLLECTION = "login_codes"
LOGIN_CODE_USES_COLLECTION = "login_code_uses"
LOGIN_CODE_IP_COLLECTION = "login_code_ip_sends"

# Used only outside production when neither LOGIN_CODE_PEPPER nor an admin token
# is configured (local dev / unit tests). Never used in production.
_DEV_PEPPER = "gen-login-code-dev-pepper"


def _resolve_pepper() -> Optional[str]:
    """HMAC pepper for stored codes.

    Order: ``LOGIN_CODE_PEPPER`` → sha256("gen-login-code-pepper:" + first admin
    token) → (non-production only) a fixed dev pepper. Returns None in production
    with nothing configured (endpoints then 503). Rotating the first admin token
    without setting LOGIN_CODE_PEPPER invalidates outstanding codes (≤10 min).
    """
    settings = get_settings()
    if settings.login_code_pepper:
        return settings.login_code_pepper
    tokens = [tok.strip() for tok in (settings.admin_tokens or "").split(",") if tok.strip()]
    if tokens:
        return hashlib.sha256(f"gen-login-code-pepper:{tokens[0]}".encode("utf-8")).hexdigest()
    if not is_production():
        return _DEV_PEPPER
    return None


def get_login_code_store(user_service: UserService = Depends(get_user_service)) -> LoginCodeStore:
    pepper = _resolve_pepper()
    if not pepper:
        logger.error("login-code: no LOGIN_CODE_PEPPER / admin token configured in production")
        raise HTTPException(status_code=503, detail="not configured")
    return LoginCodeStore(
        user_service.db,
        pepper,
        LOGIN_CODES_COLLECTION,
        LOGIN_CODE_USES_COLLECTION,
        ip_collection=LOGIN_CODE_IP_COLLECTION,
    )


# --------------------------------------------------------------------- models

class SendLoginCodeRequest(BaseModel):
    email: str
    locale: Optional[str] = None
    device_fingerprint: Optional[str] = Field(default=None, max_length=256)
    referral_code: Optional[str] = Field(default=None, max_length=64)
    # First-touch attribution, same shape/limits as SendMagicLinkRequest.attribution.
    attribution: Optional[
        Dict[Annotated[str, StringConstraints(max_length=32)], Optional[Annotated[str, StringConstraints(max_length=500)]]]
    ] = Field(default=None, max_length=16)


class SendLoginCodeResponse(BaseModel):
    status: str


class VerifyLoginCodeRequest(BaseModel):
    email: str
    code: str = Field(max_length=32)


def _sent() -> SendLoginCodeResponse:
    return SendLoginCodeResponse(status="sent")


# ------------------------------------------------------------------ endpoints

@router.post("/auth/send-code", response_model=SendLoginCodeResponse, status_code=202)
def send_login_code(
    body: SendLoginCodeRequest,
    http_request: Request,
    store: LoginCodeStore = Depends(get_login_code_store),
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """Email a 6-digit sign-in code (plus a magic link) to ``email``."""
    from backend.middleware.tenant import get_tenant_config_from_request, get_tenant_from_request

    settings = get_settings()
    email = _normalize_email(body.email)
    email_locale, ui_locale = _split_locale(body.locale)
    has_accept_language = bool(http_request.headers.get("accept-language"))
    locale = email_locale or get_locale_from_request(http_request)
    ui_locale = ui_locale or get_full_locale_from_request(http_request)
    # Locale persisted on the user at verify: only an explicit choice (body or
    # Accept-Language), never the "en" fallback — that would clobber a stored one.
    persist_locale = email_locale or (locale if has_accept_language else None)

    tenant_id = get_tenant_from_request(http_request)
    tenant_config = get_tenant_config_from_request(http_request)
    tenant_vouched = bool(tenant_config and tenant_config.is_explicitly_allowlisted(email))

    email_validation = get_email_validation_service()
    if not tenant_vouched and email_validation.is_disposable_domain(email):
        logger.warning(f"login-code: blocked disposable email {_mask_email(email)}")
        raise HTTPException(status_code=422, detail=t(locale, "users.disposableEmailBlocked"))
    if email_validation.is_email_blocked(email):
        logger.warning(f"login-code: blocked email {_mask_email(email)} — pretending success")
        return _sent()
    ip_address = get_client_ip(http_request)
    if ip_address and email_validation.is_ip_blocked(ip_address):
        logger.warning(f"login-code: blocked IP {ip_address} — pretending success")
        return _sent()

    device_fingerprint = body.device_fingerprint
    existing_user = user_service.get_user(email)
    if existing_user is None and user_service.is_signup_rate_limited(
        ip_address=ip_address, device_fingerprint=device_fingerprint,
    ):
        logger.warning(f"login-code: signup rate limit hit: IP={ip_address} for {_mask_email(email)}")
        return _sent()

    if not email_service.is_configured() and is_production():
        logger.error("login-code: email service not configured - cannot send codes")
        raise HTTPException(status_code=503, detail=t(locale, "users.emailServiceNotConfigured"))

    if tenant_config and not tenant_config.is_email_allowed(email):
        logger.warning(f"login-code: email not allowed for tenant {tenant_id}: {_mask_email(email)}")
        return _sent()

    user_agent = http_request.headers.get("user-agent")
    try:
        issued = store.issue_code(
            email,
            max_per_hour=settings.login_codes_per_email_per_hour,
            needs_credit_eval=(
                not (existing_user and getattr(existing_user, "welcome_credits_granted", False))
                and not is_test_email(email)
                and not tenant_id
            ),
            fields={
                "locale": persist_locale,
                "email_locale": locale,
                "ui_locale": ui_locale,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "device_fingerprint": device_fingerprint,
                "tenant_id": tenant_id,
                "referral_code": body.referral_code,
            },
            throttle_ip=ip_address,
            max_per_ip_per_hour=settings.login_codes_per_ip_per_hour,
        )
    except (CodeThrottled, TransactionContention):
        # Contention on one email's code doc = a burst of parallel sends to the
        # same address, which is exactly what the throttle exists to stop.
        raise HTTPException(status_code=429, detail="too_many_codes")

    # Same machinery as POST /auth/magic-link (creates the account at send time,
    # recording signup IP / fingerprint / attribution) — see module docstring.
    magic_link = user_service.create_magic_link(
        email,
        ip_address=ip_address,
        user_agent=user_agent,
        tenant_id=tenant_id,
        device_fingerprint=device_fingerprint,
        referral_code=body.referral_code,
        attribution=body.attribution,
    )
    magic_link_url = email_service.build_magic_link_url(
        magic_link.token,
        locale=locale,
        tenant_frontend_url=tenant_config.get_frontend_url() if tenant_config else None,
        referral_code=body.referral_code,
    )
    sent = email_service.send_login_code(
        email,
        issued.code,
        magic_link_url=magic_link_url,
        expiry_minutes=CODE_EXPIRY_MINUTES,
        locale=locale,
        sender_email=tenant_config.get_sender_email() if tenant_config else None,
        tenant_name=tenant_config.name if tenant_config else None,
    )
    if not sent:
        if getattr(email_service, "last_send_suppressed", False):
            logger.info(f"login-code: recipient {_mask_email(email)} is Postmark-suppressed")
        else:
            logger.error(f"login-code: failed to send code email to {_mask_email(email)}")
        # Don't reveal failure (anti-enumeration).

    # Warm the welcome-credit AI evaluation while the user reads their email, on
    # both sign-in docs (code record + magic link) so either path is instant.
    if issued.needs_credit_eval:
        threading.Thread(
            target=_precompute_credit_eval,
            args=(issued.doc_id, email, LOGIN_CODES_COLLECTION, ((MAGIC_LINKS_COLLECTION, magic_link.token),)),
            daemon=True,
        ).start()

    return _sent()


@router.post("/auth/verify-code", response_model=VerifyMagicLinkResponse)
def verify_login_code(
    body: VerifyLoginCodeRequest,
    http_request: Request,
    store: LoginCodeStore = Depends(get_login_code_store),
    user_service: UserService = Depends(get_user_service),
    email_service: EmailService = Depends(get_email_service),
):
    """Exchange an emailed code for a session (same response as GET /auth/verify)."""
    email = _normalize_email(body.email)

    outcome, record = store.verify_code(email, body.code)
    if outcome == VerifyOutcome.TOO_MANY_ATTEMPTS:
        raise HTTPException(status_code=429, detail="too_many_attempts")
    if outcome == VerifyOutcome.INVALID:
        raise HTTPException(status_code=401, detail="invalid_code")
    if outcome != VerifyOutcome.OK or record is None:
        raise HTTPException(status_code=401, detail="expired")

    tenant_id = record.get("tenant_id")
    user = user_service.get_user(email)
    if user is None:
        # Normally created at send time by create_magic_link; recreate from the
        # code record (same signup audit fields) if it was removed meanwhile.
        user = user_service.get_or_create_user(
            email,
            tenant_id=tenant_id,
            signup_ip=record.get("ip_address"),
            device_fingerprint=record.get("device_fingerprint"),
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
    persist_locale = record.get("locale")
    email_locale = persist_locale or record.get("email_locale") or user.locale or "en"

    # Tenant portal sign-ins never touch consumer credits (as in magic-link verify).
    result = complete_verified_login(
        user,
        user_service=user_service,
        email_service=email_service,
        is_first_login=is_first_login,
        precomputed_eval=precomputed_eval,
        grant_welcome_credit=not tenant_id,
        referral_code=(http_request.headers.get("x-referral-code") or record.get("referral_code"))
        if not tenant_id else None,
        locale=persist_locale,
        ui_locale=record.get("ui_locale"),
        ip_address=get_client_ip(http_request),
        user_agent=http_request.headers.get("user-agent"),
        tenant_id=tenant_id,
        device_fingerprint=record.get("device_fingerprint"),
        email_locale=email_locale,
    )
    logger.info(f"login-code: verified {_mask_email(email)} (credit_status={result.credit_status})")

    tenant_subdomain = None
    if tenant_id:
        from backend.services.tenant_service import get_tenant_service
        tenant_cfg = get_tenant_service().get_tenant_config(tenant_id)
        if tenant_cfg:
            tenant_subdomain = tenant_cfg.subdomain

    return VerifyMagicLinkResponse(
        status="success",
        session_token=result.session.token,
        user=_build_user_public(result.user),
        message=t(email_locale, "users.magicLinkSuccess"),
        tenant_subdomain=tenant_subdomain,
        credits_granted=result.credits_granted,
        credit_status=result.credit_status,
    )


# ------------------------------------------------------------- web handoff

# App → web handoff links carry a real sign-in token in the URL, so they live
# only long enough for the app to open the browser, and are single-use (the
# magic-link verify transaction marks them used).
HANDOFF_TOKEN_TTL_SECONDS = 120


class HandoffTokenRequest(BaseModel):
    # Allowlist of web destinations; Phase 1 needs only the job's lyrics review.
    target: Literal["job_review"]
    job_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    locale: Optional[str] = None


class HandoffTokenResponse(BaseModel):
    url: str
    expires_in_seconds: int


def _bearer_token(authorization: Optional[str]) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid_session")
    return token


@router.post("/auth/handoff-token", response_model=HandoffTokenResponse)
def create_handoff_token(
    body: HandoffTokenRequest,
    user_service: UserService = Depends(get_user_service),
    authorization: Optional[str] = Header(default=None),
):
    """Signed-in web link for the app's own job review (the mobile "Review on web").

    The URL is ``{FRONTEND_URL}/{locale}/auth/verify?token=…&handoff=1``: a
    ``job_review:<id>`` magic-link token valid for ``HANDOFF_TOKEN_TTL_SECONDS``
    and usable once. ``handoff=1`` makes the verify page sign in without the
    email-scanner "Complete Sign-In" tap; a used or expired link shows the
    normal "link expired" state. 401 ``invalid_session``, 404 ``not_found``
    (missing or not the caller's job), 409 ``not_in_review``.
    """
    from backend.services.job_notification_service import build_review_login_url

    token = _bearer_token(authorization)
    locale = body.locale if body.locale in SUPPORTED_LOCALES else "en"
    # Sync route (threadpool): session check, job read and mint are blocking Firestore calls.
    job, _status = get_owned_review_job(user_service, token, body.job_id)
    url = build_review_login_url(
        job.job_id, job.user_email, locale=locale, handoff_seconds=HANDOFF_TOKEN_TTL_SECONDS,
    )
    if not url:
        raise HTTPException(status_code=502, detail="link_unavailable")
    return HandoffTokenResponse(url=url, expires_in_seconds=HANDOFF_TOKEN_TTL_SECONDS)
