import pytest
from backend.services.pricing import duration_to_credits, is_blocked, SECONDS_PER_CREDIT_TIER, DURATION_CREDIT_BLOCK_SECONDS


@pytest.mark.parametrize("seconds,expected", [
    (0, 1),
    (1, 1),
    (599, 1),
    (600, 1),
    (601, 2),
    (1200, 2),
    (1201, 3),
    (3000, 5),
    (3001, 6),
    (3600, 6),
])
def test_duration_to_credits_tiers(seconds, expected):
    assert duration_to_credits(seconds) == expected


@pytest.mark.parametrize("seconds,blocked", [
    (3600, False),
    (3601, True),
    (10000, True),
    (0, False),
])
def test_is_blocked(seconds, blocked):
    assert is_blocked(seconds) is blocked


def test_constants():
    assert SECONDS_PER_CREDIT_TIER == 600
    assert DURATION_CREDIT_BLOCK_SECONDS == 3600


def test_duration_to_credits_handles_none_and_negative():
    assert duration_to_credits(None) == 1
    assert duration_to_credits(-5) == 1


def test_is_blocked_handles_none_and_negative():
    assert is_blocked(None) is False
    assert is_blocked(-5) is False
