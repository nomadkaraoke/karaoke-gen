"""Named starting states for local-stack tests (``POST /api/test/scenario``).

A scenario creates a fresh signed-in user and, optionally, a job parked in a
given state. Jobs are made through the real HTTP routes (standalone search,
then create-from-search with the user's own session token), then walked by the
fake workers with zero delay and per-user overrides. So a seeded
``awaiting_review`` job is exactly what a real user would have at that point,
not a hand-built document.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Dict, Optional

import httpx

from backend.models.job import JobStatus
from backend.services.fake_workers.checkout import public_api_url
from backend.services.fake_workers.config import get_fake_config, set_fake_config

# target -> (per-user fake config overrides, status the job must reach)
JOB_TARGETS: Dict[str, tuple] = {
    "awaiting_review": ({"step_delay_seconds": 0, "auto_approve": False}, JobStatus.AWAITING_REVIEW),
    "complete": ({"step_delay_seconds": 0, "auto_approve": True}, JobStatus.COMPLETE),
    "failed": ({"step_delay_seconds": 0, "fail_at": "lyrics"}, JobStatus.FAILED),
    # Search reports the GLOBAL duration (30 s by default, so 1 credit is charged);
    # the "downloaded" audio is 11 min (2 credits), so the real reconciliation pauses it.
    "awaiting_duration_confirm": (
        {"step_delay_seconds": 0, "audio_duration_seconds": 660},
        JobStatus.AWAITING_DURATION_CONFIRM,
    ),
}

DEFAULT_ARTIST = "Local Stack"
DEFAULT_TITLE = "Fake Song"
JOB_TIMEOUT_SECONDS = 120


class ScenarioError(Exception):
    pass


def _user_service():
    from backend.services.user_service import get_user_service

    return get_user_service()


def create_user(email: Optional[str] = None, credits: int = 2) -> Dict[str, Any]:
    """A verified user who already had their welcome credit, holding ``credits`` credits."""
    from datetime import datetime

    email = (email or f"e2e-{secrets.token_hex(4)}@example.com").strip().lower()
    users = _user_service()
    user = users.get_or_create_user(email, signup_source="local_stack")
    users.update_user(email, email_verified=True, welcome_credits_granted=True, last_login_at=datetime.utcnow())
    delta = credits - (user.credits or 0)
    if delta > 0:
        users.add_credits(email=email, amount=delta, reason="local_stack_seed")
    elif delta < 0:  # re-seeding an existing test user: set the balance outright
        users.update_user(email, credits=credits)
    session = users.create_session(email, ip_address="127.0.0.1", user_agent="local-stack-scenario")
    return {"email": email, "session_token": session.token, "credits": users.check_credits(email)}


def _api(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=public_api_url(), headers={"Authorization": f"Bearer {token}"}, timeout=60,
    )


def create_job(user: Dict[str, Any], target: str, artist: str = DEFAULT_ARTIST, title: str = DEFAULT_TITLE) -> str:
    """Create a job for ``user`` through the real routes and wait until it reaches ``target``."""
    if target not in JOB_TARGETS:
        raise ScenarioError(f"Unknown job target {target!r}; one of {sorted(JOB_TARGETS)}")
    overrides, wanted = JOB_TARGETS[target]
    set_fake_config(overrides, email=user["email"])
    # The awaiting_review job honours the user's review_mode knob (default always_review),
    # so a flow can seed "auto" to reach the confident / skipped-step states.
    review_mode = get_fake_config(email=user["email"]).review_mode if target == "awaiting_review" else "auto"

    with _api(user["session_token"]) as api:
        search = api.post("/api/audio-search/search-standalone", json={"artist": artist, "title": title})
        if search.status_code != 200:
            raise ScenarioError(f"search-standalone failed: {search.status_code} {search.text}")
        created = api.post("/api/jobs/create-from-search", json={
            "search_session_id": search.json()["search_session_id"],
            "selection_index": 0,
            "artist": artist,
            "title": title,
            "review_mode": review_mode,
        })
        if created.status_code != 200:
            raise ScenarioError(f"create-from-search failed: {created.status_code} {created.text}")
        job_id = created.json()["job_id"]

    from backend.services.job_manager import JobManager

    job_manager = JobManager()
    deadline = time.time() + JOB_TIMEOUT_SECONDS
    status = None
    while time.time() < deadline:
        job = job_manager.get_job(job_id)
        status = job.status if job else None
        if status == wanted:
            return job_id
        if status == JobStatus.FAILED and wanted != JobStatus.FAILED:
            raise ScenarioError(f"job {job_id} failed before reaching {target}: {job.error_message}")
        time.sleep(0.25)
    raise ScenarioError(f"job {job_id} did not reach {target} within {JOB_TIMEOUT_SECONDS}s (status={status})")


def run_scenario(name: str, email: Optional[str] = None, credits: Optional[int] = None,
                 artist: Optional[str] = None, title: Optional[str] = None) -> Dict[str, Any]:
    """``user`` or ``job_<target>`` (e.g. ``job_awaiting_review``)."""
    if name == "user":
        return create_user(email, 2 if credits is None else credits)
    if name.startswith("job_"):
        target = name[len("job_"):]
        if target not in JOB_TARGETS:
            raise ScenarioError(f"Unknown scenario {name!r}")
        user = create_user(email, 2 if credits is None else credits)
        job_id = create_job(user, target, artist or DEFAULT_ARTIST, title or DEFAULT_TITLE)
        return {**user, "credits": _user_service().check_credits(user["email"]), "job_id": job_id}
    raise ScenarioError(f"Unknown scenario {name!r}")


SCENARIOS = ["user"] + [f"job_{t}" for t in JOB_TARGETS]
