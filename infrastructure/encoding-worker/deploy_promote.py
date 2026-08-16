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

# Shared, pure candidate-ranking logic — the SINGLE SOURCE OF TRUTH also used by
# the runtime worker manager, so deploy green selection and runtime selection can
# never drift. Preferred import is the normal package path; when that isn't
# importable (this file is loaded by path in CI/tests, and an installed `backend`
# package can even shadow the repo working tree), fall back to loading the module
# straight from its file. It is stdlib-only, so by-path exec has no side effects.
try:
    from backend.services.encoding_worker_preference import (
        PRIMARY_MACHINE_TYPE,
        ordered_candidates,
    )
except ImportError:  # pragma: no cover - exercised in the bare CI heredoc / shadowed env
    import importlib.util
    import pathlib

    _pref_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "backend" / "services" / "encoding_worker_preference.py"
    )
    _spec = importlib.util.spec_from_file_location("encoding_worker_preference", _pref_path)
    _pref = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_pref)
    PRIMARY_MACHINE_TYPE = _pref.PRIMARY_MACHINE_TYPE
    ordered_candidates = _pref.ordered_candidates


def select_green_candidates(
    config: Dict[str, Any],
    fallback_vms: List[Dict[str, Any]],
) -> List[Dict[str, Optional[str]]]:
    """Ranked list of candidate green (staging) targets for the workflow to try.

    Pool = the c4d ``secondary_vm`` (kind="secondary") + all fallback VMs
    (kind="fallback"), EXCLUDING the current ``active_override_vm``. The pool is
    ranked by the shared preference logic — fastest-first, demoting any type that
    recently stocked out (per ``config["capacity_state"]``). So:
      * when c4d has capacity, the c4d secondary sorts first → normal blue-green;
      * during a c4d stockout, the secondary is demoted and a deep-pool fallback
        (n2d/n2/c2d) leads, instead of wasting ~2 min probing a c4d that won't boot.

    Excluding the current override is what makes the deploy zero-downtime: the
    green is always a *different*, freshly-booted worker, so the override keeps
    serving until the workflow atomically switches to the validated green.

    Each candidate: {"vm", "ip", "zone", "machine_type", "kind"}.
    """
    pool: List[Dict[str, Optional[str]]] = []

    secondary_vm = config.get("secondary_vm")
    if secondary_vm:
        pool.append({
            "vm": secondary_vm,
            "ip": config.get("secondary_ip"),
            "zone": config.get("secondary_zone") or DEFAULT_C4D_ZONE,
            # secondary is always the c4d pair unless the config says otherwise.
            "machine_type": config.get("secondary_machine_type") or PRIMARY_MACHINE_TYPE,
            "kind": "secondary",
        })

    override_vm = config.get("active_override_vm")
    # Only complete entries are usable — a fallback missing vm/zone/ip would
    # produce an unstartable candidate (e.g. `gcloud ... --zone=None`) and
    # silently drop capacity from the selection path.
    usable = [
        f for f in (fallback_vms or [])
        if f.get("vm") and f.get("zone") and f.get("ip") and f.get("vm") != override_vm
    ]
    for f in usable:
        pool.append({
            "vm": f["vm"],
            "ip": f.get("ip"),
            "zone": f.get("zone"),
            "machine_type": f.get("machine_type"),  # inferred from vm name if absent
            "kind": "fallback",
        })

    return ordered_candidates(pool, capacity_state=config.get("capacity_state"))


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

    def _finalize(updates: Dict[str, Any]) -> Dict[str, Any]:
        # Never drain/stop the worker we just promoted, whatever the config held
        # (e.g. a stale override that pointed at the green, or primary==secondary).
        safe = [t for t in drain if t.get("vm") and t["vm"] != green["vm"]]
        return {"firestore_updates": updates, "drain_and_stop": safe}

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
        return _finalize(updates)

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
    return _finalize(updates)
