"""gen → karaoke-decide partner calls (``X-Partner-Secret``).

decide already authenticates to gen with ``DECIDE_GEN_PARTNER_SECRET``; account
deletion uses the same secret in the other direction to erase a gen user's
decide data. The host is gen's existing decide setting
(``karaoke_decide_api_url``, default https://decide.nomadkaraoke.com), which is
decide's main API (the catalog proxy uses the same host).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30
USERS_PATH = "/api/partner/gen/users"


class DecidePartnerError(RuntimeError):
    """decide is unconfigured or answered non-2xx."""


class DecidePartnerClient:
    def __init__(self, base_url: Optional[str] = None, secret: Optional[str] = None, http_client=None):
        settings = get_settings()
        self.base_url = (base_url if base_url is not None else settings.karaoke_decide_api_url or "").rstrip("/")
        self.secret = secret if secret is not None else settings.decide_partner_secret
        self._http_client = http_client  # injectable httpx.Client for tests

    def delete_user(self, email: str) -> dict:
        """Erase every decide store for the gen user ``email``. Idempotent on decide's side.

        Returns decide's ``{"deleted": {collection: count}}``. Raises
        :class:`DecidePartnerError` when unconfigured or on any non-2xx.
        """
        if not self.base_url or not self.secret:
            raise DecidePartnerError("decide partner API not configured (karaoke_decide_api_url / DECIDE_GEN_PARTNER_SECRET)")
        url = f"{self.base_url}{USERS_PATH}"
        headers = {"X-Partner-Secret": self.secret}
        body = {"email": email.strip().lower()}
        try:
            if self._http_client is not None:
                resp = self._http_client.request("DELETE", url, json=body, headers=headers)
            else:
                with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
                    resp = client.request("DELETE", url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise DecidePartnerError(f"decide delete request failed: {type(e).__name__}") from e
        if not 200 <= resp.status_code < 300:
            raise DecidePartnerError(f"decide delete returned HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}
