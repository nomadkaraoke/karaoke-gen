"""Unit tests for the shared encoding-worker candidate preference logic.

This pure module drives BOTH runtime selection and deploy green selection, so its
ordering guarantees (fastest-first, cooldown demotion, machine_type inference) are
the contract both call-sites rely on.
"""
from datetime import datetime, timedelta, timezone

from backend.services.encoding_worker_preference import (
    COOLDOWN_SECONDS,
    SPEED_RANK,
    UNKNOWN_RANK,
    cooldown_key,
    infer_machine_type,
    ordered_candidates,
)

NOW = datetime(2026, 8, 15, 19, 0, 0, tzinfo=timezone.utc)


def _c(vm, zone, machine_type=None, **extra):
    d = {"vm": vm, "zone": zone, "ip": f"10.0.0.{hash(vm) % 250 + 1}"}
    if machine_type is not None:
        d["machine_type"] = machine_type
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# machine_type inference
# ---------------------------------------------------------------------------

def test_explicit_machine_type_wins():
    assert infer_machine_type(_c("whatever", "z", "c2d-highcpu-32")) == "c2d-highcpu-32"


def test_infer_from_vm_name_specific_before_generic():
    # "n2d"/"c4d" must not be shadowed by "n2"/"c4".
    assert infer_machine_type(_c("encoding-worker-fallback-n2da", "z")) == "n2d-highcpu-32"
    assert infer_machine_type(_c("encoding-worker-fallback-n2f", "z")) == "n2-highcpu-32"
    assert infer_machine_type(_c("encoding-worker-fallback-c4x", "z")) == "c4-highcpu-32"


def test_infer_unknown_returns_none():
    # Bare a/b c4d fallbacks (no family token) can't be inferred from the name.
    assert infer_machine_type(_c("encoding-worker-fallback-a", "z")) is None


def test_runtime_dict_shape_uses_vm_name_key():
    assert infer_machine_type({"vm_name": "x-n2d-y", "zone": "z"}) == "n2d-highcpu-32"


# ---------------------------------------------------------------------------
# fastest-first ordering
# ---------------------------------------------------------------------------

def test_orders_fastest_first():
    pool = [
        _c("n2", "us-central1-c", "n2-highcpu-32"),
        _c("c4d", "us-central1-c", "c4d-highcpu-32"),
        _c("c2d", "us-central1-f", "c2d-highcpu-32"),
        _c("c4", "us-central1-a", "c4-highcpu-32"),
    ]
    got = [c["vm"] for c in ordered_candidates(pool, now=NOW)]
    assert got == ["c4d", "c4", "c2d", "n2"]


def test_unknown_type_sorts_last_but_kept():
    pool = [
        _c("mystery", "z"),  # no machine_type, unknown name
        _c("c4d", "z", "c4d-highcpu-32"),
    ]
    got = [c["vm"] for c in ordered_candidates(pool, now=NOW)]
    assert got == ["c4d", "mystery"]
    assert SPEED_RANK["c4d-highcpu-32"] < UNKNOWN_RANK


def test_stable_within_equal_rank_preserves_zone_spread():
    # Two n2 in different zones keep input order (caller's secondary preference).
    pool = [
        _c("n2f", "us-central1-f", "n2-highcpu-32"),
        _c("n2c", "us-central1-c", "n2-highcpu-32"),
    ]
    got = [c["vm"] for c in ordered_candidates(pool, now=NOW)]
    assert got == ["n2f", "n2c"]


# ---------------------------------------------------------------------------
# cooldown / availability awareness
# ---------------------------------------------------------------------------

def test_recent_stockout_demotes_type_to_back():
    pool = [
        _c("c4d", "us-central1-c", "c4d-highcpu-32"),
        _c("c4", "us-central1-a", "c4-highcpu-32"),
        _c("n2", "us-central1-c", "n2-highcpu-32"),
    ]
    # c4d stocked out 1 minute ago → demoted behind the still-healthy types.
    capacity_state = {"c4d-highcpu-32@us-central1-c": (NOW - timedelta(minutes=1)).isoformat()}
    got = [c["vm"] for c in ordered_candidates(pool, capacity_state, now=NOW)]
    assert got == ["c4", "n2", "c4d"]


def test_cooldown_expires_and_snaps_back_to_fastest():
    pool = [
        _c("c4d", "us-central1-c", "c4d-highcpu-32"),
        _c("c4", "us-central1-a", "c4-highcpu-32"),
    ]
    # Stockout older than COOLDOWN → c4d is hot again and returns to the top.
    old = (NOW - timedelta(seconds=COOLDOWN_SECONDS + 60)).isoformat()
    capacity_state = {"c4d-highcpu-32@us-central1-c": old}
    got = [c["vm"] for c in ordered_candidates(pool, capacity_state, now=NOW)]
    assert got == ["c4d", "c4"]


def test_two_types_stocked_out_still_serves_from_deep_pool():
    # Both newest-gen types (c4d, c4) exhausted; deep pools keep serving in order.
    pool = [
        _c("c4d", "us-central1-c", "c4d-highcpu-32"),
        _c("c4", "us-central1-a", "c4-highcpu-32"),
        _c("c2d", "us-central1-f", "c2d-highcpu-32"),
        _c("n2d", "us-central1-a", "n2d-highcpu-32"),
    ]
    recent = (NOW - timedelta(minutes=2)).isoformat()
    capacity_state = {
        "c4d-highcpu-32@us-central1-c": recent,
        "c4-highcpu-32@us-central1-a": recent,
    }
    got = [c["vm"] for c in ordered_candidates(pool, capacity_state, now=NOW)]
    # Hot deep pools first (fastest-first among them), then the two cold types.
    assert got == ["c2d", "n2d", "c4d", "c4"]


def test_type_only_capacity_key_demotes_all_zones():
    pool = [
        _c("c4d-c", "us-central1-c", "c4d-highcpu-32"),
        _c("n2", "us-central1-c", "n2-highcpu-32"),
    ]
    capacity_state = {"c4d-highcpu-32": (NOW - timedelta(minutes=1)).isoformat()}
    got = [c["vm"] for c in ordered_candidates(pool, capacity_state, now=NOW)]
    assert got == ["n2", "c4d-c"]


def test_empty_capacity_state_is_pure_speed_order():
    pool = [_c("n2", "z", "n2-highcpu-32"), _c("c4d", "z", "c4d-highcpu-32")]
    assert [c["vm"] for c in ordered_candidates(pool, {}, now=NOW)] == ["c4d", "n2"]
    assert [c["vm"] for c in ordered_candidates(pool, None, now=NOW)] == ["c4d", "n2"]


def test_naive_timestamp_tolerated():
    pool = [_c("c4d", "us-central1-c", "c4d-highcpu-32"), _c("n2", "z", "n2-highcpu-32")]
    naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None).isoformat()
    capacity_state = {"c4d-highcpu-32@us-central1-c": naive}
    got = [c["vm"] for c in ordered_candidates(pool, capacity_state, now=NOW)]
    assert got == ["n2", "c4d"]


def test_malformed_timestamp_ignored():
    pool = [_c("c4d", "us-central1-c", "c4d-highcpu-32"), _c("n2", "z", "n2-highcpu-32")]
    capacity_state = {"c4d-highcpu-32@us-central1-c": "not-a-date"}
    got = [c["vm"] for c in ordered_candidates(pool, capacity_state, now=NOW)]
    assert got == ["c4d", "n2"]  # unparseable → not in cooldown


def test_empty_pool():
    assert ordered_candidates([], now=NOW) == []


def test_cooldown_key_shape():
    assert cooldown_key(_c("x", "us-central1-c", "c4d-highcpu-32")) == "c4d-highcpu-32@us-central1-c"
    assert cooldown_key(_c("x", None, "n2-highcpu-32")) == "n2-highcpu-32"
    assert cooldown_key(_c("no-token", "z")) is None
