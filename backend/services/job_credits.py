"""Single source of truth for how many consumer credits a job has been charged.

``JobManager.create_job`` records ``state_data.credits_charged`` (and
``payment_bypassed=True`` with ``credits_charged=0`` for admin / tenant /
no-email jobs). Duration reconciliation, audio selection, confirm-duration and
the refund paths then keep that running total up to date.

Every reader MUST go through :func:`get_credits_charged` /
:func:`is_payment_bypassed` so they all agree on the value — in particular for
jobs that lack the field. Before v0.278.2 several create paths (create-from-search,
the audio-search create endpoint, made-for-you orders) replaced the whole
``state_data`` map right after ``create_job`` and so wiped ``credits_charged``
and ``payment_bypassed``. Readers disagreed about the missing value (reconcile
assumed 1, confirm-duration assumed 0, the stale-review refund assumed 0), so a
paused 15-minute job asked the client to acknowledge 1 credit for a 2-credit job
and 409'd when it acknowledged the true total.

Legacy default (field missing): we infer what ``create_job`` actually debited,
using the same signals it used to decide whether to charge:

* ``payment_bypassed`` flag present            → 0
* no ``user_email``                            → 0 (create_job never charges)
* ``creation_params.is_admin`` / admin email   → 0 (admin & made-for-you jobs)
* ``tenant_id`` set                            → 0 (tenant-billed; a spoofed,
  non-allowlisted tenant header was charged 1, but we deliberately bias toward
  NOT refunding credits that may never have been debited)
* otherwise                                    → 1 (every wiping path charged the
  default ``JobCreate.credits == 1``; jobs that charged more — URL jobs with a
  known duration — always kept the field)
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

# state_data keys that carry billing state. Anything that resets/clears
# state_data (retry-from-scratch, admin source override) must carry these over,
# or a later reconcile/refund would re-derive the charge from scratch.
BILLING_STATE_KEYS = ("credits_charged", "payment_bypassed", "select_charge_applied")


def _legacy_unbilled(job: Any) -> bool:
    """True if a job lacking ``credits_charged`` was never charged by create_job."""
    user_email = getattr(job, "user_email", None)
    if not user_email:
        return True
    creation_params = getattr(job, "creation_params", None) or {}
    if isinstance(creation_params, Mapping) and creation_params.get("is_admin"):
        return True
    tenant_id = getattr(job, "tenant_id", None)
    if isinstance(tenant_id, str) and tenant_id:
        return True
    try:
        from backend.services.auth_service import is_admin_email

        if isinstance(user_email, str) and is_admin_email(user_email):
            return True
    except Exception:  # pragma: no cover - defensive; never block billing reads
        pass
    return False


def get_credits_charged(job: Any) -> int:
    """Total consumer credits debited for this job so far (see module docstring)."""
    state = getattr(job, "state_data", None) or {}
    value = state.get("credits_charged")
    if value is not None:
        return int(value)
    if state.get("payment_bypassed"):
        return 0
    return 0 if _legacy_unbilled(job) else 1


def is_payment_bypassed(job: Any) -> bool:
    """True if this job is not billed in consumer credits (admin/tenant/no-email).

    Uses the explicit flag, falling back to the legacy inference for jobs whose
    ``payment_bypassed`` flag was wiped along with ``credits_charged``.
    """
    state = getattr(job, "state_data", None) or {}
    if state.get("payment_bypassed"):
        return True
    if state.get("credits_charged") is None:
        return _legacy_unbilled(job)
    return False


def preserve_billing_state(state_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Subset of ``state_data`` that must survive a state_data reset."""
    state = state_data or {}
    return {k: state[k] for k in BILLING_STATE_KEYS if k in state}
