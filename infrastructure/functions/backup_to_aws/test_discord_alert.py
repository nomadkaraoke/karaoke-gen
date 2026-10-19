"""Tests for discord_alert: #alerts vs #noise webhook resolution + delivery."""

from unittest.mock import MagicMock, patch

import requests

import discord_alert

ALERT = "https://discord.com/api/webhooks/1/alert"
NOISE = "https://discord.com/api/webhooks/2/noise"


def _ok():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    return resp


def test_send_alert_returns_true_on_success():
    with patch("discord_alert.requests.post", return_value=_ok()) as post:
        assert discord_alert.send_alert(ALERT, "t", [], success=True) is True
    assert post.call_args.args[0] == ALERT


def test_send_alert_returns_false_on_failure():
    bad = MagicMock()
    bad.raise_for_status.side_effect = requests.HTTPError("404")
    with patch("discord_alert.requests.post", return_value=bad):
        assert discord_alert.send_alert(ALERT, "t", [], success=False) is False


def test_noise_url_env_var_wins(monkeypatch):
    monkeypatch.setenv("DISCORD_NOISE_WEBHOOK_URL", NOISE)
    with patch("discord_alert.secretmanager") as sm:
        assert discord_alert.get_noise_webhook_url("proj", ALERT) == NOISE
    sm.SecretManagerServiceClient.assert_not_called()


def test_noise_url_read_from_secret_manager(monkeypatch):
    monkeypatch.delenv("DISCORD_NOISE_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_NOISE_WEBHOOK_SECRET_NAME", raising=False)
    with patch("discord_alert.secretmanager") as sm:
        client = sm.SecretManagerServiceClient.return_value
        client.access_secret_version.return_value.payload.data = (NOISE + "\n").encode()
        assert discord_alert.get_noise_webhook_url("proj", ALERT) == NOISE
    name = client.access_secret_version.call_args.kwargs["request"]["name"]
    assert name == "projects/proj/secrets/discord-noise-webhook/versions/latest"


def test_noise_url_falls_back_to_alert_on_secret_error(monkeypatch):
    monkeypatch.delenv("DISCORD_NOISE_WEBHOOK_URL", raising=False)
    with patch("discord_alert.secretmanager") as sm:
        sm.SecretManagerServiceClient.return_value.access_secret_version.side_effect = RuntimeError("403")
        assert discord_alert.get_noise_webhook_url("proj", ALERT) == ALERT


def test_noise_url_falls_back_to_alert_when_secret_empty(monkeypatch):
    monkeypatch.delenv("DISCORD_NOISE_WEBHOOK_URL", raising=False)
    with patch("discord_alert.secretmanager") as sm:
        sm.SecretManagerServiceClient.return_value.access_secret_version.return_value.payload.data = b" "
        assert discord_alert.get_noise_webhook_url("proj", ALERT) == ALERT


def test_send_noise_posts_to_noise_webhook():
    with patch("discord_alert.send_alert", return_value=True) as send:
        assert discord_alert.send_noise(NOISE, ALERT, "t", []) is True
    send.assert_called_once_with(webhook_url=NOISE, title="t", fields=[], success=True)


def test_send_noise_falls_back_to_alert_webhook_on_failure():
    with patch("discord_alert.send_alert", side_effect=[False, True]) as send:
        assert discord_alert.send_noise(NOISE, ALERT, "t", []) is True
    assert [c.kwargs["webhook_url"] for c in send.call_args_list] == [NOISE, ALERT]


def test_send_noise_no_retry_when_noise_is_alert():
    with patch("discord_alert.send_alert", return_value=False) as send:
        assert discord_alert.send_noise(ALERT, ALERT, "t", []) is False
    send.assert_called_once()
