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
from backend.models.tenant import TenantAuth, TenantConfig
from backend.services.job_manager import JobManager


@pytest.fixture
def user_service():
    us = MagicMock()
    us.check_credits.return_value = 0  # a tenant user with no consumer credits
    us.deduct_credits.return_value = (True, 0, "ok")
    return us


TENANTS = {
    "randy-vild": TenantConfig(
        id="randy-vild",
        name="Randy Vild",
        subdomain="randy-vild.nomadkaraoke.com",
        auth=TenantAuth(allowed_emails=["randyvild@gmail.com"], require_email_domain=True),
    ),
    "dormant": TenantConfig(
        id="dormant",
        name="Dormant",
        subdomain="dormant.nomadkaraoke.com",
        is_active=False,
        auth=TenantAuth(allowed_emails=["randyvild@gmail.com"], require_email_domain=True),
    ),
}


@pytest.fixture
def manager(user_service):
    tenant_service = MagicMock()
    tenant_service.get_tenant_config.side_effect = lambda tid: TENANTS.get(tid)
    with patch("backend.services.job_manager.FirestoreService"), \
         patch("backend.services.job_manager.StorageService"), \
         patch("backend.services.user_service.get_user_service", return_value=user_service), \
         patch("backend.services.tenant_service.get_tenant_service", return_value=tenant_service):
        jm = JobManager()
        yield jm


def _create(tenant_id="", email="randyvild@gmail.com"):
    return JobCreate(
        artist="Randy Vild",
        title="Simulation",
        theme_id="randy-vild",
        user_email=email,
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


@pytest.mark.parametrize(
    "tenant_id,email",
    [
        ("randy-vild", "attacker@gmail.com"),  # spoofed X-Tenant-ID, not on the allowlist
        ("no-such-tenant", "randyvild@gmail.com"),  # unknown tenant
        ("dormant", "randyvild@gmail.com"),  # inactive tenant
    ],
)
def test_tenant_context_without_membership_is_charged(manager, user_service, tenant_id, email):
    """Tenant context comes from a client-controlled header, so it only waives
    consumer credits for users actually allowed on that tenant's portal."""
    user_service.check_credits.return_value = 5
    job = manager.create_job(_create(tenant_id=tenant_id, email=email))
    user_service.deduct_credits.assert_called_once()
    assert job.state_data["credits_charged"] == 1
