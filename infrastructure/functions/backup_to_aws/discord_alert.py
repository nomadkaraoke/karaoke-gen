"""Discord webhook notifications for backup status.

Two channels:

* **#alerts** (``DISCORD_WEBHOOK_URL`` env, from secret ``discord-alert-webhook``)
  — real problems only: a failed or partial backup run.
* **#noise** (secret ``discord-noise-webhook``, read at runtime) — the routine
  "all good" nightly report. If the noise webhook can't be resolved, or a noise
  post fails, the message falls back to #alerts so nothing is silently dropped.
"""

import logging
import os

import requests
from google.cloud import secretmanager

logger = logging.getLogger(__name__)

NOISE_WEBHOOK_SECRET_DEFAULT = "discord-noise-webhook"


def send_alert(webhook_url: str, title: str, fields: list[dict], success: bool = True) -> bool:
    """Send a Discord embed notification. Returns True if delivered."""
    color = 0x00FF00 if success else 0xFF0000
    embed = {"title": title, "color": color, "fields": fields}

    try:
        resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Discord alert: {e}")
        return False


def get_noise_webhook_url(project: str, fallback_url: str) -> str:
    """Resolve the #noise webhook URL, falling back to ``fallback_url``.

    ``DISCORD_NOISE_WEBHOOK_URL`` env first, else Secret Manager secret
    ``discord-noise-webhook`` (name overridable via
    ``DISCORD_NOISE_WEBHOOK_SECRET_NAME``). Read at runtime rather than as a
    deploy-time secret env var, so no Pulumi change is needed; the function's
    SA holds project-wide ``secretmanager.secretAccessor``. Any error → fallback.
    """
    url = os.environ.get("DISCORD_NOISE_WEBHOOK_URL", "").strip()
    if url:
        return url
    secret_id = os.environ.get("DISCORD_NOISE_WEBHOOK_SECRET_NAME", NOISE_WEBHOOK_SECRET_DEFAULT)
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{secret_id}/versions/latest"
        resp = client.access_secret_version(request={"name": name})
        url = resp.payload.data.decode("utf-8").strip()
    except Exception as e:  # noqa: BLE001 — noise channel is optional
        logger.warning(f"Could not read {secret_id} ({e}); routine reports go to the alert webhook")
        return fallback_url
    return url or fallback_url


def send_noise(noise_url: str, alert_url: str, title: str, fields: list[dict]) -> bool:
    """Post an informational (success) embed to #noise; on failure retry on #alerts."""
    if noise_url and send_alert(webhook_url=noise_url, title=title, fields=fields, success=True):
        return True
    if alert_url and alert_url != noise_url:
        logger.warning("Noise webhook post failed; falling back to the alert webhook")
        return send_alert(webhook_url=alert_url, title=title, fields=fields, success=True)
    return False
