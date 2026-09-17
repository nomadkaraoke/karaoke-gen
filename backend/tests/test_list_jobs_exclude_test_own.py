"""
Regression tests for list_jobs' exclude_test filter and the caller's own jobs.

An admin account that is *itself* a test account (the e2e-test-runner the CI
canary impersonates) must still see the jobs it just created in its own
"My Jobs" list. The exclude_test filter hides test-account jobs from admin
dashboards, but it must never hide the CALLER's own jobs — otherwise the
happy-path E2E breaks immediately after creating its job (STEP 5: the new job
card never appears in Recent Jobs).
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.api.routes.jobs import list_jobs
from backend.services.auth_service import AuthResult, UserType


E2E_RUNNER = "e2e-test-runner@nomadkaraoke.com"

JOBS = [
    {"job_id": "own1", "user_email": E2E_RUNNER},               # caller's own (test acct)
    {"job_id": "othertest", "user_email": "x@inbox.testmail.app"},  # other test acct
    {"job_id": "real", "user_email": "real@example.com"},        # real user
]


def _admin(email: str) -> AuthResult:
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=0,
        message="",
        user_email=email,
        is_admin=True,
        tenant_id="",
    )


async def _call_summary(auth: AuthResult):
    request = MagicMock()
    with patch("backend.api.routes.jobs.get_locale_from_request", return_value="en"), \
         patch("backend.api.routes.jobs.get_tenant_from_request", return_value=""), \
         patch("backend.api.routes.jobs.job_manager") as mock_jm:
        # Return a fresh copy each call so pruning doesn't mutate shared state.
        mock_jm.list_jobs_summary.return_value = [dict(j) for j in JOBS]
        return await list_jobs(request, fields="summary", auth_result=auth)


@pytest.mark.asyncio
async def test_e2e_runner_sees_its_own_jobs():
    """The e2e-test-runner admin must still see its own test-account jobs."""
    result = await _call_summary(_admin(E2E_RUNNER))
    ids = {j["job_id"] for j in result}
    assert "own1" in ids          # caller's own job kept
    assert "real" in ids          # real job kept
    assert "othertest" not in ids  # OTHER test-account job still hidden


@pytest.mark.asyncio
async def test_regular_admin_still_hides_all_test_jobs():
    """A normal admin (not a test account) still has all test jobs filtered out."""
    result = await _call_summary(_admin("andrew@nomadkaraoke.com"))
    ids = {j["job_id"] for j in result}
    assert "real" in ids
    assert "own1" not in ids       # e2e-test-runner jobs hidden from real admin
    assert "othertest" not in ids  # testmail jobs hidden
