"""Revoke Sign in with Apple tokens (App Store guideline 5.1.1(v)).

Account deletion exchanges the app's one-time ``authorization_code`` for
tokens, then revokes them so the app disappears from the user's Apple ID
"Sign in with Apple" list. Best-effort: never raises; a stale code must not
block erasure.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
import jwt

from backend.config import get_settings

logger = logging.getLogger(__name__)

APPLE_AUDIENCE = "https://appleid.apple.com"
TOKEN_URL = f"{APPLE_AUDIENCE}/auth/token"
REVOKE_URL = f"{APPLE_AUDIENCE}/auth/revoke"
_CLIENT_SECRET_TTL_SECONDS = 300
_HTTP_TIMEOUT_SECONDS = 10


class AppleRevoker:
    def __init__(self, settings=None):
        self._settings = settings or get_settings()

    def _client_secret(self) -> str:
        s = self._settings
        now = int(time.time())
        return jwt.encode(
            {
                "iss": s.apple_team_id,
                "iat": now,
                "exp": now + _CLIENT_SECRET_TTL_SECONDS,
                "aud": APPLE_AUDIENCE,
                "sub": s.apple_client_id,
            },
            s.apple_siwa_key_p8,
            algorithm="ES256",
            headers={"kid": s.apple_siwa_key_id},
        )

    async def revoke(self, authorization_code: Optional[str]) -> bool:
        """Exchange the code and revoke the resulting token. True only if Apple revoked it."""
        s = self._settings
        if not (s.apple_siwa_key_p8 and s.apple_siwa_key_id):
            logger.warning("Apple revocation skipped: APPLE_SIWA_KEY_P8/APPLE_SIWA_KEY_ID not configured")
            return False
        if not authorization_code:
            logger.warning("Apple revocation skipped: no authorization code")
            return False
        try:
            secret = self._client_secret()
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "client_id": s.apple_client_id,
                        "client_secret": secret,
                        "code": authorization_code,
                        "grant_type": "authorization_code",
                    },
                )
                if resp.status_code != 200:
                    logger.warning("Apple token exchange failed: HTTP %s", resp.status_code)
                    return False
                body = resp.json()
                refresh = body.get("refresh_token")
                token = refresh or body.get("access_token")
                if not token:
                    logger.warning("Apple token exchange returned no token")
                    return False
                resp = await client.post(
                    REVOKE_URL,
                    data={
                        "client_id": s.apple_client_id,
                        "client_secret": secret,
                        "token": token,
                        "token_type_hint": "refresh_token" if refresh else "access_token",
                    },
                )
                if resp.status_code != 200:
                    logger.warning("Apple token revoke failed: HTTP %s", resp.status_code)
                    return False
            return True
        except Exception as e:  # never raise into account deletion
            logger.warning("Apple revocation error: %s", type(e).__name__)
            return False
