"""Pure decision logic for the encoding-worker blue-green deploy (used by CI).

The GitHub Actions workflow (`.github/workflows/ci.yml`) does all the GCP I/O
(`gcloud compute instances start/stop`, health `curl`s, Firestore writes). It
delegates the *decisions* to the pure functions here so the branching — the part
that's easy to get wrong and impossible to run locally in CI — has real unit
tests (`backend/tests/test_deploy_promote.py`).

Background: us-central1 is in a persistent c4d Spot stockout, so the c4d
primary/secondary are usually down and all traffic runs on an n2 fallback
recorded as `active_override_vm`. A naive "start the c4d secondary and swap"
deploy can't start the secondary and never refreshes the serving fallback, so
worker-side changes never reach production. These helpers make the deploy pick a
*fresh* green target (c4d secondary if it starts, else a different n2 fallback)
and then promote that validated green to serving with zero downtime.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# c4d/n2 primary+secondary live here; the worker manager uses the same zone.
DEFAULT_C4D_ZONE = "us-central1-c"


def _is_n2(vm_name: str) -> bool:
    """True for the non-Spot n2 fallbacks (e.g. encoding-worker-fallback-n2c/-n2f).

    n2 fallbacks are on-demand (reliable to start); c4d fallbacks are Spot and
    share the stockout, so we prefer n2 as a green target.
    """
    return "n2" in (vm_name or "")


def select_green_candidates(
    config: Dict[str, Any],
    fallback_vms: List[Dict[str, Any]],
) -> List[Dict[str, Optional[str]]]:
    """Ordered list of candidate green (staging) targets for the workflow to try.

    Order:
      1. the c4d `secondary_vm` (kind="secondary") — keeps the normal blue-green
         behaviour when c4d capacity is available;
      2. fallback VMs (kind="fallback"), EXCLUDING the current `active_override_vm`,
         with n2 fallbacks before c4d fallbacks.

    Excluding the current override is what makes the deploy zero-downtime: the
    green is always a *different*, freshly-booted worker, so the override keeps
    serving until the workflow atomically switches to the validated green.

    Each candidate: {"vm", "ip", "zone", "kind"}.
    """
    candidates: List[Dict[str, Optional[str]]] = []

    secondary_vm = config.get("secondary_vm")
    if secondary_vm:
        candidates.append({
            "vm": secondary_vm,
            "ip": config.get("secondary_ip"),
            "zone": config.get("secondary_zone") or DEFAULT_C4D_ZONE,
            "kind": "secondary",
        })

    override_vm = config.get("active_override_vm")
    usable = [
        f for f in (fallback_vms or [])
        if f.get("vm") and f.get("vm") != override_vm
    ]
    # n2 (non-Spot) first, then stable name order for determinism.
    usable.sort(key=lambda f: (0 if _is_n2(f["vm"]) else 1, f["vm"]))
    for f in usable:
        candidates.append({
            "vm": f["vm"],
            "ip": f.get("ip"),
            "zone": f.get("zone"),
            "kind": "fallback",
        })

    return candidates


def promote_plan(
    green: Dict[str, Any],
    config: Dict[str, Any],
    *,
    now: str,
    version: str,
) -> Dict[str, Any]:
    """Given a *validated* green worker, decide how to make it the serving worker.

    Returns {"firestore_updates": {...}, "drain_and_stop": [{"vm","ip","zone"}, ...]}.
    The workflow applies the Firestore updates in one transaction, then drains
    (waits for active_jobs==0, bounded) and stops each VM in `drain_and_stop`.

    Two cases:
      * green.kind == "secondary": standard blue-green swap (green→primary, old
        primary→secondary); ALSO clear any `active_override_*` so traffic returns
        to the fresh c4d primary. Drain+stop the old primary and the old override.
      * green.kind == "fallback": the green becomes the `active_override` (the
        serving worker). Leave primary/secondary untouched so c4d resumes as
        primary when capacity returns. Drain+stop the previous override (if any,
        and different from the green).
    """
    drain: List[Dict[str, Optional[str]]] = []

    if green["kind"] == "secondary":
        updates: Dict[str, Any] = {
            "primary_vm": green["vm"],
            "primary_ip": green["ip"],
            "primary_version": version,
            "primary_deployed_at": now,
            "secondary_vm": config.get("primary_vm"),
            "secondary_ip": config.get("primary_ip"),
            "secondary_version": config.get("primary_version"),
            "secondary_deployed_at": now,
            "last_swap_at": now,
            "deploy_in_progress": False,
            "deploy_in_progress_since": None,
        }
        old_primary = config.get("primary_vm")
        if old_primary:
            drain.append({
                "vm": old_primary,
                "ip": config.get("primary_ip"),
                "zone": config.get("primary_zone") or DEFAULT_C4D_ZONE,
            })
        # A stale override must be cleared so active_url follows the fresh primary.
        if config.get("active_override_vm"):
            updates.update({
                "active_override_vm": None,
                "active_override_ip": None,
                "active_override_zone": None,
                "active_override_set_at": None,
                "active_override_version": None,
            })
            drain.append({
                "vm": config["active_override_vm"],
                "ip": config.get("active_override_ip"),
                "zone": config.get("active_override_zone"),
            })
        return {"firestore_updates": updates, "drain_and_stop": drain}

    # kind == "fallback": the green becomes the serving override.
    updates = {
        "active_override_vm": green["vm"],
        "active_override_ip": green["ip"],
        "active_override_zone": green["zone"],
        "active_override_set_at": now,
        "active_override_version": version,
        "deploy_in_progress": False,
        "deploy_in_progress_since": None,
    }
    old_override = config.get("active_override_vm")
    if old_override and old_override != green["vm"]:
        drain.append({
            "vm": old_override,
            "ip": config.get("active_override_ip"),
            "zone": config.get("active_override_zone"),
        })
    return {"firestore_updates": updates, "drain_and_stop": drain}
