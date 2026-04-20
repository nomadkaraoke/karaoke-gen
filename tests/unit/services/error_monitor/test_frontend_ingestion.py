"""Unit tests for frontend error ingestion."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.services.error_monitor.frontend_ingestion import (
    FrontendErrorReport,
    RateLimiter,
    build_pattern_data,
    sanitize_url,
)


def test_sanitize_url_strips_query_and_fragment():
    assert sanitize_url("https://gen.nomadkaraoke.com/en/app/jobs/?token=abc#/xyz/review") == "https://gen.nomadkaraoke.com/en/app/jobs/"


def test_sanitize_url_keeps_path_and_preserves_hash_route_prefix():
    # For our hash-routed pages we want #/ retained as a coarse indicator, but
    # the job id inside it is sensitive-ish — drop the fragment entirely.
    assert sanitize_url("https://gen.nomadkaraoke.com/en/app/jobs/#/abcdef/review") == "https://gen.nomadkaraoke.com/en/app/jobs/"


def test_sanitize_url_handles_bad_input():
    assert sanitize_url("") == ""
    assert sanitize_url("not a url") == "not a url"[:512]


def test_build_pattern_data_uses_stack_when_present():
    report = FrontendErrorReport(
        message="TypeError: Cannot read properties of null",
        stack="TypeError: Cannot read properties of null\n  at foo (app.js:1:2)\n  at bar (app.js:3:4)",
        url="https://gen.nomadkaraoke.com/en/app/jobs/",
        user_agent="Mozilla/5.0 (Android 14; Mobile; rv:138.0) Gecko/138.0 Firefox/138.0",
        release="abc1234",
        user_email=None,
        viewport={"w": 412, "h": 915},
        locale="en",
        extra=None,
    )

    data = build_pattern_data(report, now=datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc))

    assert data.service == "frontend"
    assert data.resource_type == "browser"
    # normalized_message is what was hashed — stack-based so same message from
    # different URLs/ids collapses.
    assert "TypeError" in data.normalized_message
    assert data.sample_message.startswith("TypeError: Cannot read properties of null")
    assert data.count == 1
    # pattern_id is deterministic for same stack
    data2 = build_pattern_data(report, now=datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc))
    assert data.pattern_id == data2.pattern_id


def test_build_pattern_data_falls_back_to_message_when_no_stack():
    report = FrontendErrorReport(
        message="Network request failed",
        stack=None,
        url="https://gen.nomadkaraoke.com/en/app/",
        user_agent="ua",
        release="abc",
        user_email="x@y.com",
        viewport=None,
        locale="en",
        extra=None,
    )
    data = build_pattern_data(report, now=datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc))
    assert "Network request failed" in data.normalized_message


def test_different_errors_produce_different_pattern_ids():
    a = FrontendErrorReport(
        message="A", stack="A\n  at foo", url="", user_agent="", release="", user_email=None,
        viewport=None, locale="en", extra=None,
    )
    b = FrontendErrorReport(
        message="B", stack="B\n  at bar", url="", user_agent="", release="", user_email=None,
        viewport=None, locale="en", extra=None,
    )
    assert build_pattern_data(a, now=datetime.now(tz=timezone.utc)).pattern_id != build_pattern_data(b, now=datetime.now(tz=timezone.utc)).pattern_id


def test_rate_limiter_allows_up_to_max():
    rl = RateLimiter(max_per_minute=3)
    for i in range(3):
        assert rl.allow("1.2.3.4", 1000.0 + i) is True


def test_rate_limiter_blocks_when_over_limit():
    rl = RateLimiter(max_per_minute=3)
    for _ in range(3):
        assert rl.allow("1.2.3.4", 1000.0) is True
    assert rl.allow("1.2.3.4", 1000.5) is False


def test_rate_limiter_allows_again_after_window_expires():
    rl = RateLimiter(max_per_minute=2)
    assert rl.allow("1.2.3.4", 1000.0) is True
    assert rl.allow("1.2.3.4", 1000.1) is True
    assert rl.allow("1.2.3.4", 1000.2) is False
    # 61 seconds later — both earlier hits have expired from the 60s window
    assert rl.allow("1.2.3.4", 1061.0) is True


def test_rate_limiter_tracks_ips_independently():
    rl = RateLimiter(max_per_minute=2)
    assert rl.allow("1.1.1.1", 1000.0) is True
    assert rl.allow("1.1.1.1", 1000.0) is True
    assert rl.allow("1.1.1.1", 1000.0) is False
    # Different IP should not be affected
    assert rl.allow("2.2.2.2", 1000.0) is True
