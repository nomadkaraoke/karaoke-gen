"""Google and Apple ID-token verification for mobile-app sign-in.

The app gets an ID token from the native Google / Apple SDK and exchanges it at
``POST /api/users/auth/google|apple`` for a gen session. Here we check the
token's RS256 signature against the provider's JWKS, its issuer, audience and
expiry, and that the email is verified. Every failure raises
:class:`InvalidIdentityToken` (the route answers 401 ``invalid_token``).

JWKS fetches are blocking network calls: callers run this off the event loop
(the sign-in routes are sync ``def`` routes, served from the threadpool).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, Literal, Sequence

import jwt

logger = logging.getLogger(__name__)

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUERS = ("https://appleid.apple.com",)

# Clock skew tolerated on exp / iat (seconds).
_LEEWAY_SECONDS = 60
# Provider keys rotate rarely; PyJWKClient caches them and refetches on an unknown kid.
_JWKS_CACHE_SECONDS = 6 * 60 * 60

_JWKS_CLIENTS: Dict[str, "jwt.PyJWKClient"] = {}
_JWKS_LOCK = threading.Lock()

Provider = Literal["google", "apple"]


class InvalidIdentityToken(Exception):
    """The ID token is malformed, forged, expired, for another app, or unverified."""


@dataclass(frozen=True)
class VerifiedIdentity:
    email: str  # lowercased
    provider: Provider
    subject: str  # the provider's stable user id ("sub")


def parse_audiences(raw: str) -> list[str]:
    """Comma-separated config value → list of non-empty audiences."""
    return [a.strip() for a in (raw or "").split(",") if a.strip()]


def _jwks_client(url: str) -> "jwt.PyJWKClient":
    """One cached ``PyJWKClient`` per JWKS URL (it caches the keys itself)."""
    with _JWKS_LOCK:
        client = _JWKS_CLIENTS.get(url)
        if client is None:
            client = jwt.PyJWKClient(url, cache_keys=True, lifespan=_JWKS_CACHE_SECONDS, timeout=10)
            _JWKS_CLIENTS[url] = client
        return client


def _verify(
    id_token: str,
    *,
    provider: Provider,
    jwks_url: str,
    issuers: Iterable[str],
    audiences: Sequence[str],
) -> VerifiedIdentity:
    if not audiences:
        raise InvalidIdentityToken("no accepted audience configured")
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=list(audiences),
            leeway=_LEEWAY_SECONDS,
            options={"require": ["iss", "aud", "sub", "exp", "iat"]},
        )
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise InvalidIdentityToken(f"{provider} token rejected: {type(e).__name__}") from e

    if claims.get("iss") not in tuple(issuers):
        raise InvalidIdentityToken(f"{provider} token has the wrong issuer")
    # Google sends a bool; Apple has sent both a bool and the string "true".
    if claims.get("email_verified") not in (True, "true"):
        raise InvalidIdentityToken(f"{provider} token email is not verified")
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise InvalidIdentityToken(f"{provider} token has no email")
    return VerifiedIdentity(email=email.strip().lower(), provider=provider, subject=str(claims["sub"]))


def verify_google_id_token(id_token: str, audiences: Sequence[str]) -> VerifiedIdentity:
    return _verify(id_token, provider="google", jwks_url=GOOGLE_JWKS_URL,
                   issuers=GOOGLE_ISSUERS, audiences=audiences)


def verify_apple_id_token(id_token: str, audiences: Sequence[str]) -> VerifiedIdentity:
    return _verify(id_token, provider="apple", jwks_url=APPLE_JWKS_URL,
                   issuers=APPLE_ISSUERS, audiences=audiences)


FAKE_TOKEN_PREFIX = "fake:"


def fake_identity(id_token: str, provider: Provider) -> VerifiedIdentity:
    """``fake:<email>`` → identity, for the local stack ONLY.

    The caller must check fake mode (FAKE_WORKERS, refused in production) first;
    outside it a ``fake:`` token is just an invalid token.
    """
    email = id_token[len(FAKE_TOKEN_PREFIX):].strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain:
        raise InvalidIdentityToken("fake token needs an email")
    return VerifiedIdentity(email=email, provider=provider, subject=f"fake:{email}")
