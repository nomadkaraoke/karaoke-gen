"""Tenant portals are shared workspaces.

Andrew (2026-11-11): "my expectation is that all jobs in the tenant portal are
accessible to everyone who has permission to access that portal."

Before this, a non-admin only ever saw/opened jobs whose ``user_email`` was their
own, even on a tenant portal. A client (Randy) logging in with his own address
never saw the 10 jobs Andrew had bulk-uploaded for him under another address,
and re-submitted every song. Now any member of a tenant's allowlist can list and
open all of that tenant's jobs; consumer jobs stay private to their owner.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.models.tenant import TenantAuth, TenantConfig
from backend.services import job_access
from backend.services.auth_service import AuthResult, UserType

MEMBER = "client@gmail.com"
TEAMMATE = "colleague@client-label.com"
OUTSIDER = "someone@else.com"


def _tenant(active=True, emails=(MEMBER,), domains=("client-label.com",)):
    return TenantConfig(
        id="client", name="Client", subdomain="client.nomadkaraoke.com", is_active=active,
        auth=TenantAuth(allowed_emails=list(emails), allowed_email_domains=list(domains)),
    )


@pytest.fixture
def tenant_service():
    svc = MagicMock()
    svc.get_tenant_config.side_effect = lambda tid: _tenant() if tid == "client" else None
    with patch("backend.services.tenant_service.get_tenant_service", return_value=svc):
        yield svc


def _job(owner="bulk@nomadkaraoke.com", tenant_id="client"):
    return SimpleNamespace(user_email=owner, tenant_id=tenant_id, job_id="j1")


def _user(email, admin=False):
    return AuthResult(
        is_valid=True, user_type=UserType.ADMIN if admin else UserType.UNLIMITED,
        remaining_uses=-1, message="", user_email=email, is_admin=admin, tenant_id="client",
    )


class TestCanAccessJob:
    def test_member_can_access_teammates_tenant_job(self, tenant_service):
        assert job_access.can_access_job(_job(), MEMBER, False)

    def test_domain_member_can_access(self, tenant_service):
        assert job_access.can_access_job(_job(), TEAMMATE, False)

    def test_member_check_is_case_insensitive(self, tenant_service):
        assert job_access.can_access_job(_job(), MEMBER.upper(), False)

    def test_outsider_denied(self, tenant_service):
        assert not job_access.can_access_job(_job(), OUTSIDER, False)

    def test_consumer_jobs_stay_private(self, tenant_service):
        assert not job_access.can_access_job(_job(owner="a@b.com", tenant_id=""), MEMBER, False)

    def test_member_of_one_tenant_cannot_see_another(self, tenant_service):
        assert not job_access.can_access_job(_job(tenant_id="other"), MEMBER, False)

    def test_inactive_tenant_not_shared(self, tenant_service):
        tenant_service.get_tenant_config.side_effect = lambda tid: _tenant(active=False)
        assert not job_access.can_access_job(_job(), MEMBER, False)

    def test_tenant_without_allowlist_not_shared(self, tenant_service):
        tenant_service.get_tenant_config.side_effect = lambda tid: _tenant(emails=(), domains=())
        assert not job_access.can_access_job(_job(), MEMBER, False)

    def test_removed_member_loses_access(self, tenant_service):
        tenant_service.get_tenant_config.side_effect = lambda tid: _tenant(emails=(), domains=("x.com",))
        assert not job_access.can_access_job(_job(), MEMBER, False)

    def test_lookup_error_fails_closed(self, tenant_service):
        tenant_service.get_tenant_config.side_effect = RuntimeError("boom")
        assert not job_access.can_access_job(_job(), MEMBER, False)

    def test_owner_and_admin_unchanged(self, tenant_service):
        assert job_access.can_access_job(_job(owner=OUTSIDER, tenant_id=""), OUTSIDER, False)
        assert job_access.can_access_job(_job(owner="x@y.com", tenant_id=""), None, True)

    def test_token_without_email_denied_on_owned_job(self, tenant_service):
        assert not job_access.can_access_job(_job(), None, False)

    def test_legacy_ownerless_job_still_accessible(self, tenant_service):
        assert job_access.can_access_job(_job(owner=None, tenant_id=""), OUTSIDER, False)

    def test_ownerless_can_be_denied_per_route(self, tenant_service):
        """Style-upload routes never allowed ownerless jobs to non-admins; keep that."""
        assert not job_access.can_access_job(
            _job(owner=None, tenant_id=""), OUTSIDER, False, allow_ownerless=False)
        # ...but a tenant member still gets the tenant's ownerless jobs.
        assert job_access.can_access_job(_job(owner=None), MEMBER, False, allow_ownerless=False)


class TestListJobsOnTenantPortal:
    JOBS = [{"job_id": "bulk1", "user_email": "bulk@nomadkaraoke.com"},
            {"job_id": "mine", "user_email": MEMBER}]

    def _call(self, auth, tenant_id):
        from backend.api.routes.jobs import list_jobs
        with patch("backend.api.routes.jobs.get_locale_from_request", return_value="en"), \
             patch("backend.api.routes.jobs.get_tenant_from_request", return_value=tenant_id), \
             patch("backend.api.routes.jobs.job_manager") as jm:
            jm.list_jobs_summary.return_value = [dict(j) for j in self.JOBS]
            list_jobs(MagicMock(), fields="summary", auth_result=auth)
            return jm.list_jobs_summary.call_args.kwargs

    def test_member_lists_all_tenant_jobs(self, tenant_service):
        kwargs = self._call(_user(MEMBER), "client")
        assert kwargs["user_email"] is None
        assert kwargs["tenant_id"] == "client"   # still scoped to this tenant

    def test_outsider_on_portal_only_lists_own(self, tenant_service):
        kwargs = self._call(_user(OUTSIDER), "client")
        assert kwargs["user_email"] == OUTSIDER

    def test_consumer_portal_still_own_jobs_only(self, tenant_service):
        kwargs = self._call(_user(MEMBER), None)
        assert kwargs["user_email"] == MEMBER
        assert kwargs["tenant_id"] == ""


class TestReviewAuthOnTenantJob:
    def _call(self, email):
        from backend.api.dependencies import require_review_auth_factory
        dep = require_review_auth_factory()
        auth_service = MagicMock()
        auth_service.validate_token_full.return_value = _user(email)
        jm = MagicMock()
        jm.get_job.return_value = _job()
        creds = SimpleNamespace(credentials="tok")
        with patch("backend.services.job_manager.JobManager", return_value=jm):
            return asyncio.run(dep(request=MagicMock(), job_id="j1", review_token=None,
                                   auth_service=auth_service, credentials=creds, token=None))

    def test_member_can_open_review(self, tenant_service):
        assert self._call(MEMBER) == ("j1", "full")

    def test_outsider_gets_403(self, tenant_service):
        with pytest.raises(HTTPException) as e:
            self._call(OUTSIDER)
        assert e.value.status_code == 403


class TestCheckJobOwnershipWrapper:
    def test_uses_shared_rule(self, tenant_service):
        from backend.api.routes.jobs import _check_job_ownership
        assert _check_job_ownership(_job(), _user(MEMBER))
        assert not _check_job_ownership(_job(), _user(OUTSIDER))
