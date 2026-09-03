"""Tests for the board-specific signup rate limit (IP cap raised, fingerprint strict)."""
from unittest.mock import MagicMock

from backend.services.user_service import UserService, _safe_positive_int_env


def _service(ip_count=0, fp_count=0):
    svc = UserService.__new__(UserService)  # bypass __init__ (no real Firestore client)
    svc.count_recent_signups_from_ip = MagicMock(return_value=ip_count)
    svc.count_recent_signups_from_fingerprint = MagicMock(return_value=fp_count)
    return svc


def test_default_ip_cap_blocks_at_two():
    svc = _service(ip_count=2)
    assert svc.is_signup_rate_limited(ip_address="1.2.3.4") is True
    svc = _service(ip_count=1)
    assert svc.is_signup_rate_limited(ip_address="1.2.3.4") is False


def test_board_override_raises_ip_cap():
    # 10 prior signups from this IP: blocked by default (2), allowed under board cap (30).
    svc = _service(ip_count=10)
    assert svc.is_signup_rate_limited(ip_address="1.2.3.4") is True
    assert svc.is_signup_rate_limited(ip_address="1.2.3.4", max_signups=30) is False


def test_board_override_does_not_relax_fingerprint_cap():
    # Same browser (fingerprint) with 5 prior signups is blocked even under the board
    # IP override — a venue is many devices on one IP, not many accounts on one device.
    svc = _service(ip_count=0, fp_count=5)
    assert svc.is_signup_rate_limited(device_fingerprint="fp", max_signups=30) is True


def test_safe_positive_int_env(monkeypatch):
    monkeypatch.delenv("BOARD_MAX_SIGNUPS_PER_IP", raising=False)
    assert _safe_positive_int_env("BOARD_MAX_SIGNUPS_PER_IP", 30) == 30
    monkeypatch.setenv("BOARD_MAX_SIGNUPS_PER_IP", "50")
    assert _safe_positive_int_env("BOARD_MAX_SIGNUPS_PER_IP", 30) == 50
    for bad in ("", "abc", "0", "-5"):
        monkeypatch.setenv("BOARD_MAX_SIGNUPS_PER_IP", bad)
        assert _safe_positive_int_env("BOARD_MAX_SIGNUPS_PER_IP", 30) == 30
