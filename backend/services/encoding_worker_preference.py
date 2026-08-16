"""Shared, pure preference logic for encoding-worker candidate ordering.

This is the SINGLE SOURCE OF TRUTH for how encode-worker VM candidates are ranked,
used by BOTH:

  * runtime worker selection —
    ``backend/services/encoding_service._build_worker_candidates`` →
    ``encoding_worker_manager.ensure_any_running``
  * deploy green (staging) selection —
    ``infrastructure/encoding-worker/deploy_promote.select_green_candidates``

Keeping the ordering in one place stops the two call-sites from drifting (they
previously used different ad-hoc heuristics: runtime = secret order, deploy =
"n2 before c4d").

Design:
  * **Fastest-first base order.** Candidates sort by ``SPEED_RANK`` (lower =
    faster), so the fastest available type (c4d) is always preferred when it can
    start — the whole reason c4d was chosen. No behaviour change on a healthy day.
  * **Availability-aware cooldown.** A type that recently failed to start with a
    capacity/stockout error (recorded in Firestore ``capacity_state``) is
    *demoted to the back* for ``COOLDOWN_SECONDS`` — long enough to stop paying
    the ~2-min-per-attempt probe cost on a known-dead type, short enough to
    re-probe and snap back to the faster type the moment capacity returns. It is
    demoted, never dropped, so the pool always stays fully available.

This module is intentionally **dependency-free (stdlib only)** so that
``deploy_promote`` — which is loaded by path in CI with no backend package deps —
can import it without pulling the heavy backend tree.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Lower rank = faster = preferred. Seeded from CPU architecture; refine with the
# measured medians from infrastructure/encoding-worker/benchmark_types.py.
#   c4d  AMD EPYC Turin (Zen5)      — current primary, fastest
#   c4   Intel Emerald Rapids       — newest Intel perf tier
#   n4d  AMD (Titanium)             — flexible/deep AMD
#   c2d  AMD Milan (Zen3)           — mature, deep pool
#   n2d  AMD Rome/Milan             — deep pool (32 GB floor)
#   n2   Intel Cascade/Ice Lake     — current fallback (32 GB floor)
SPEED_RANK: Dict[str, int] = {
    "c4d-highcpu-32": 10,
    "c4-highcpu-32": 20,
    "n4d-highcpu-32": 30,
    "c2d-highcpu-32": 50,
    "n2d-highcpu-32": 60,
    "n2-highcpu-32": 70,
}

# Rank for a candidate whose machine type is unknown and cannot be inferred:
# treat as slowest-but-usable so it sorts LAST without ever being dropped.
UNKNOWN_RANK = 1000

# How long a type that just stocked out is demoted before it is re-probed.
COOLDOWN_SECONDS = 900  # 15 minutes

# Machine type of the blue-green primary/secondary pair (encoding-worker-a/-b).
# They are always c4d; used when a candidate carries no explicit machine_type.
PRIMARY_MACHINE_TYPE = "c4d-highcpu-32"

# vm-name substring → machine type, for backward-compat inference when a fallback
# entry predates the machine_type field. Checked in order — longer / more-specific
# family tokens FIRST so "n2d" is not shadowed by "n2", nor "c4d" by "c4".
_NAME_TOKEN_TO_TYPE = (
    ("c4d", "c4d-highcpu-32"),
    ("n4d", "n4d-highcpu-32"),
    ("n2d", "n2d-highcpu-32"),
    ("c2d", "c2d-highcpu-32"),
    ("c4", "c4-highcpu-32"),
    ("n2", "n2-highcpu-32"),
)


def _candidate_name(candidate: Dict[str, Any]) -> str:
    """VM name from either dict shape ({"vm"} for deploy, {"vm_name"} for runtime)."""
    return (candidate.get("vm") or candidate.get("vm_name") or "")


def infer_machine_type(candidate: Dict[str, Any]) -> Optional[str]:
    """Best-effort machine type: explicit ``machine_type`` field, else vm-name token.

    Fallback secret entries created before the ``machine_type`` field carry only
    vm/zone/ip; the VM-name family token (…-fallback-n2c, …-fallback-c4a) is the
    only hint. Returns None when nothing matches (caller treats as UNKNOWN_RANK).
    """
    explicit = candidate.get("machine_type")
    if explicit:
        return explicit
    name = _candidate_name(candidate).lower()
    for token, mtype in _NAME_TOKEN_TO_TYPE:
        if token in name:
            return mtype
    return None


def _speed_rank(candidate: Dict[str, Any]) -> int:
    return SPEED_RANK.get(infer_machine_type(candidate), UNKNOWN_RANK)


def cooldown_key(candidate: Dict[str, Any]) -> Optional[str]:
    """Firestore ``capacity_state`` key for a candidate: ``"<machine_type>@<zone>"``.

    Zone-scoped because a stockout is per (machine family × zone). Returns None
    when the machine type can't be determined (no cooldown tracking possible).
    NOTE: keys deliberately avoid ``.`` so they are safe as Firestore map keys.
    """
    mtype = infer_machine_type(candidate)
    if not mtype:
        return None
    zone = candidate.get("zone")
    return f"{mtype}@{zone}" if zone else mtype


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:  # tolerate naive timestamps — assume UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _recently_stocked_out(
    candidate: Dict[str, Any],
    capacity_state: Optional[Dict[str, Any]],
    now: datetime,
    cooldown_seconds: float,
) -> bool:
    """True if this candidate's (type@zone) — or its coarser type-only key — is in cooldown."""
    if not capacity_state:
        return False
    key = cooldown_key(candidate)
    if not key:
        return False
    ts = _parse_iso(capacity_state.get(key))
    if ts is None:
        # Fall back to a type-only signal (a coarser "this family is short").
        mtype = infer_machine_type(candidate)
        if mtype:
            ts = _parse_iso(capacity_state.get(mtype))
    if ts is None:
        return False
    return (now - ts).total_seconds() < cooldown_seconds


def ordered_candidates(
    pool: List[Dict[str, Any]],
    capacity_state: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    cooldown_seconds: float = COOLDOWN_SECONDS,
) -> List[Dict[str, Any]]:
    """Rank ``pool`` fastest-first, demoting recently-stocked-out types to the back.

    Args:
        pool: candidate dicts, each ``{vm|vm_name, zone, ip, machine_type?, kind?}``.
            Objects are returned as-is (not copied), only reordered — so callers can
            round-trip their own richer objects through a dict view.
        capacity_state: Firestore map ``{"<type>@<zone>"|"<type>": last_stockout_iso}``.
            Absent/empty ⇒ pure fastest-first order (today's behaviour).
        now: current UTC time (injectable for tests); defaults to ``datetime.now(UTC)``.
        cooldown_seconds: demotion window; defaults to ``COOLDOWN_SECONDS``.

    The speed sort is STABLE, so equal-rank candidates keep their input order —
    callers express a secondary preference (e.g. zone spread) purely via input
    ordering. The hot/cold partition is likewise stable within each group.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ranked = sorted(pool, key=_speed_rank)  # stable: fastest first
    hot = [c for c in ranked if not _recently_stocked_out(c, capacity_state, now, cooldown_seconds)]
    cold = [c for c in ranked if _recently_stocked_out(c, capacity_state, now, cooldown_seconds)]
    return hot + cold
