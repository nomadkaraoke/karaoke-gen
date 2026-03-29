"""Discord webhook notifications for backup status."""

import logging
import requests

logger = logging.getLogger(__name__)


def send_alert(webhook_url: str, title: str, fields: list[dict], success: bool = True):
    """Send a Discord embed notification."""
    color = 0x00FF00 if success else 0xFF0000
    embed = {"title": title, "color": color, "fields": fields}

    try:
        resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Discord alert: {e}")
