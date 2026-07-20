"""
Edge origin-lock middleware.

Cloudflare fronts ``api.nomadkaraoke.com`` (WAF + rate limiting). To stop
attackers from bypassing the edge by hitting the Cloud Run origin directly
(``*.run.app`` / ``ghs.googlehosted.com``), Cloudflare injects a shared-secret
header (``X-Edge-Auth``) on every proxied request via a Transform Rule. This
middleware rejects public requests that lack the correct header.

A Cloud Run *domain mapping* can't use ``ingress=internal-and-cloud-load-balancing``
(that needs a load balancer), so this app-layer check is how we lock the origin.

Modes (env ``EDGE_AUTH_MODE``, default ``off``):
    off      - passthrough, no checks (deploy-dormant; behaviour unchanged).
    warn     - log requests missing/failing the header, but ALLOW them. Use to
               confirm nothing legit is missing the header before enforcing.
    enforce  - reject requests missing/failing the header with 403.

Secret: env ``EDGE_ORIGIN_SECRET`` (same value Cloudflare injects). If enforce
mode is set but the secret is unconfigured, the middleware FAILS OPEN and logs
an error — a misconfiguration must not take the API down.

Exemptions (always allowed, even in enforce mode):
    * Health / root endpoints and Cloud Run probes, which hit the origin
      directly (not via Cloudflare) and legitimately lack the header.
"""

import hmac
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Header Cloudflare injects (must match CloudflareConfig.ORIGIN_AUTH_HEADER in
# infrastructure/modules/edge_security.py).
EDGE_AUTH_HEADER = "X-Edge-Auth"

# Paths always allowed without the edge header. These are non-sensitive and are
# probed directly on the origin by Cloud Run startup/liveness checks and GCP
# uptime monitoring, which don't traverse Cloudflare.
_EXEMPT_EXACT = {"/", "/api/health", "/api/health/"}
_EXEMPT_PREFIXES = ("/api/health/",)

_VALID_MODES = {"off", "warn", "enforce"}


def _mode() -> str:
    mode = os.environ.get("EDGE_AUTH_MODE", "off").lower().strip()
    return mode if mode in _VALID_MODES else "off"


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


class EdgeAuthMiddleware(BaseHTTPMiddleware):
    """Reject origin requests that didn't come through the Cloudflare edge."""

    async def dispatch(self, request: Request, call_next) -> Response:
        mode = _mode()
        if mode == "off":
            return await call_next(request)

        if _is_exempt(request.url.path):
            return await call_next(request)

        secret = os.environ.get("EDGE_ORIGIN_SECRET", "")
        if not secret:
            # Fail open on misconfiguration — never take the API down because a
            # secret wasn't wired up. Loud error so it's caught in staging.
            logger.error(
                "EDGE_AUTH_MODE=%s but EDGE_ORIGIN_SECRET is unset — failing "
                "open (allowing request). Origin lock is NOT active.",
                mode,
            )
            return await call_next(request)

        provided = request.headers.get(EDGE_AUTH_HEADER, "")
        # Constant-time comparison to avoid leaking the secret via timing.
        ok = bool(provided) and hmac.compare_digest(provided, secret)

        if ok:
            return await call_next(request)

        client = request.client.host if request.client else "?"
        if mode == "warn":
            logger.warning(
                "edge-auth: request missing/invalid %s header (warn mode, "
                "allowing). path=%s ip=%s",
                EDGE_AUTH_HEADER, request.url.path, client,
            )
            return await call_next(request)

        # enforce
        logger.warning(
            "edge-auth: blocked direct-to-origin request. path=%s ip=%s",
            request.url.path, client,
        )
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
