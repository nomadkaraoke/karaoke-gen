"""App Review demo sign-in: one configured email signs in with a fixed code."""
from __future__ import annotations

import logging

import pytest

from backend.api.routes import login_code
from backend.tests.test_login_code_api import (  # noqa: F401  (fixtures)
    EMAIL,
    _verify,
    client,
    db,
    email_service,
    precompute,
    settings,
    signup_limited,
    user_service,
    validation,
)

DEMO_EMAIL = "reviewer@example.com"
DEMO_CODE = "424242"


@pytest.fixture
def demo(settings):
    settings.app_review_demo_email = DEMO_EMAIL
    settings.app_review_demo_code = DEMO_CODE
    return settings


def test_demo_code_inert_when_unset(client, settings):
    # Neither field configured on the settings object at all.
    assert _verify(client, DEMO_CODE, email=DEMO_EMAIL).status_code == 401
    settings.app_review_demo_email = DEMO_EMAIL
    settings.app_review_demo_code = ""
    assert _verify(client, "", email=DEMO_EMAIL).status_code == 401
    settings.app_review_demo_email = ""
    settings.app_review_demo_code = DEMO_CODE
    assert _verify(client, DEMO_CODE, email="").status_code in (401, 422)


def test_demo_code_only_for_demo_email(client, demo):
    resp = _verify(client, DEMO_CODE, email="someone-else@example.com")
    assert resp.status_code == 401


def test_demo_code_signs_in(client, demo, caplog):
    with caplog.at_level(logging.INFO, logger=login_code.logger.name):
        resp = _verify(client, DEMO_CODE, email=DEMO_EMAIL.upper())
    assert resp.status_code == 200
    assert resp.json()["session_token"]
    assert "app review demo login" in caplog.text
    # Reusable, and wrong codes for the demo email are rejected.
    assert _verify(client, DEMO_CODE, email=DEMO_EMAIL).status_code == 200
    assert _verify(client, "111111", email=DEMO_EMAIL).status_code == 401


def test_attempt_limit_still_applies(client, demo):
    for _ in range(5):
        assert _verify(client, "111111", email=DEMO_EMAIL).status_code == 401
    # Even the right code is refused once locked out.
    assert _verify(client, DEMO_CODE, email=DEMO_EMAIL).status_code == 429
