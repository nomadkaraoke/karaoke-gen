"""Who may access a job.

One rule for every route that guards a job:

- Nomad admins can access every job.
- The job's owner (``job.user_email``) can access it.
- Tenant portals are shared workspaces: anyone currently allowed to sign in to a
  tenant's portal (its ``allowed_emails`` / ``allowed_email_domains`` allowlist)
  can access every job of that tenant, whoever submitted it. Membership is
  re-checked on every call against the live (cached ~5 min) tenant config, so
  removing someone from the allowlist also removes their access to the jobs.
- Consumer jobs (no tenant) stay private to their owner.

Andrew's expectation (2026-11-11): "all jobs in the tenant portal are accessible to
everyone who has permission to access that portal". Before this, a client logging in
with their own email couldn't see jobs bulk-uploaded for them under another address.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def is_tenant_member(tenant_id: Optional[str], user_email: Optional[str]) -> bool:
    """True if ``user_email`` may currently use ``tenant_id``'s (active) portal."""
    if not tenant_id or not user_email:
        return False
    try:
        from backend.services.tenant_service import get_tenant_service

        config = get_tenant_service().get_tenant_config(tenant_id)
    except Exception as e:  # noqa: BLE001 - fail closed: no shared access on lookup errors
        logger.warning(f"Tenant {tenant_id}: config lookup failed for shared-access check: {e}")
        return False
    if not config or not config.is_active:
        return False
    return config.is_email_allowed(user_email)


def can_access_job(
    job,
    user_email: Optional[str],
    is_admin: bool,
    allow_ownerless: bool = True,
) -> bool:
    """Whether a caller may view and act on ``job`` (see module docstring).

    ``allow_ownerless`` keeps each route's pre-existing stance on legacy jobs with
    no ``user_email``: most routes (job routes, review auth) allowed them; the
    style-upload routes did not, and pass False.
    """
    if is_admin:
        return True

    job_email = getattr(job, "user_email", None)
    if user_email and job_email and user_email.lower() == job_email.lower():
        return True

    if user_email and is_tenant_member(getattr(job, "tenant_id", None), user_email):
        return True

    if job_email:
        # Someone else's job (or token auth without an email): no access.
        return False

    # Legacy jobs without an owner: unchanged per-route behaviour.
    return allow_ownerless
