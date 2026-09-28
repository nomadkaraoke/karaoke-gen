"""
Tenant portal users never touch consumer credits.

Tenants (companies / bulk clients) are billed under a separate commercial
agreement, so:
- tenant jobs are never credit-checked or charged, and are marked
  payment_bypassed (so duration reconciliation never pauses them for credits
  and refunds never mint credits they didn't pay),
- consumer jobs are still charged exactly as before.
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.models.job import JobCreate
from backend.services.job_manager import JobManager


@pytest.fixture
def user_service():
    us = MagicMock()
    us.check_credits.return_value = 0  # a tenant user with no consumer credits
    us.deduct_credits.return_value = (True, 0, "ok")
    return us


@pytest.fixture
def manager(user_service):
    with patch("backend.services.job_manager.FirestoreService"), \
         patch("backend.services.job_manager.StorageService"), \
         patch("backend.services.user_service.get_user_service", return_value=user_service):
        jm = JobManager()
        yield jm


def _create(tenant_id=""):
    return JobCreate(
        artist="Randy Vild",
        title="Simulation",
        theme_id="randy-vild",
        user_email="randyvild@gmail.com",
        tenant_id=tenant_id,
    )


def test_tenant_job_not_charged_even_with_zero_credits(manager, user_service):
    job = manager.create_job(_create(tenant_id="randy-vild"))
    user_service.check_credits.assert_not_called()
    user_service.deduct_credits.assert_not_called()
    assert job.state_data["credits_charged"] == 0
    assert job.state_data["payment_bypassed"] is True


def test_consumer_job_still_charged(manager, user_service):
    user_service.check_credits.return_value = 5
    job = manager.create_job(_create())
    user_service.check_credits.assert_called_once_with("randyvild@gmail.com")
    user_service.deduct_credits.assert_called_once()
    assert job.state_data["credits_charged"] == 1
    assert "payment_bypassed" not in job.state_data


def test_consumer_job_without_credits_rejected(manager, user_service):
    from backend.exceptions import InsufficientCreditsError

    with pytest.raises(InsufficientCreditsError):
        manager.create_job(_create())
