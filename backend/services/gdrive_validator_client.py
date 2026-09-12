"""
GDrive Validator Cloud Function client.

Lightweight HTTP client for invoking the gdrive-validator Cloud Function
from the backend (e.g., after job completion). Uses OIDC authentication
via the backend's service account.

Fire-and-forget pattern: never blocks or fails the caller.
"""
import logging
import os

import requests
import google.auth.transport.requests
import google.oauth2.id_token

logger = logging.getLogger(__name__)

# Cloud Function URL - set via environment variable
GDRIVE_VALIDATOR_URL = os.environ.get("GDRIVE_VALIDATOR_URL", "")


def trigger_gdrive_validation(brand_code: str | None = None) -> dict | None:
    """
    Invoke the GDrive validator Cloud Function.

    Uses the backend service account's OIDC token for authentication.

    Args:
        brand_code: Optional brand code of the just-published track. When set, the
            validator additionally verifies this track is present in all expected
            folders same-run (incident-hardening D2).

    Returns:
        Parsed JSON response from the function, or None on error.
    """
    if not GDRIVE_VALIDATOR_URL:
        logger.debug("GDRIVE_VALIDATOR_URL not set, skipping GDrive validation trigger")
        return None

    try:
        # Get OIDC token for the Cloud Function URL
        auth_request = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(auth_request, GDRIVE_VALIDATOR_URL)

        response = requests.post(
            GDRIVE_VALIDATOR_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"brand_code": brand_code} if brand_code else None,
            timeout=60,
        )
        response.raise_for_status()

        result = response.json()
        status = result.get("status", "unknown")

        if status == "issues_found":
            logger.warning("GDrive validation found issues (notification sent)")
        elif status == "ok":
            logger.info("GDrive validation: all clear")
        else:
            logger.info(f"GDrive validation returned status: {status}")

        return result

    except Exception:
        logger.exception("Failed to trigger GDrive validation (non-fatal)")
        return None
