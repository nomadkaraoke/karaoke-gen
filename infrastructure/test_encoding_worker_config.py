"""Invariants for the encoding-worker capacity-fallback fleet config.

Guards the machine-family diversification added 2026-08-12 after a region-wide
c4d-highcpu-32 ZONE_RESOURCE_POOL_EXHAUSTED stockout (us-central1-a/-b/-c at once)
exhausted every same-family lane and forced slow local encoding (→ 524 on
preview, parked renders).

Run locally with: `pytest infrastructure/test_encoding_worker_config.py`
(infrastructure is not part of the CI backend test gate — Pulumi validates the
resource graph via `pulumi preview`).
"""

from config import EncodingWorkerConfig, MachineTypes


def test_fallback_fleet_has_machine_family_diversity():
    """At least one fallback must use a machine family OTHER than the primary,
    otherwise a single-family stockout takes out every lane at once."""
    families = {fb["machine_type"] for fb in EncodingWorkerConfig.FALLBACKS}
    assert MachineTypes.ENCODING_WORKER in families
    assert any(mt != MachineTypes.ENCODING_WORKER for mt in families), (
        "fallback fleet is single-machine-family — a c4d stockout would exhaust "
        "every lane (see incident 2026-08-12)"
    )
    assert MachineTypes.ENCODING_WORKER_ALT in families


def test_n2_fallbacks_use_pd_balanced_disk():
    """n2 does not support hyperdisk-balanced; those VMs must use pd-balanced,
    else Pulumi/GCE rejects the instance at create time."""
    for fb in EncodingWorkerConfig.FALLBACKS:
        if fb["machine_type"].startswith("n2"):
            assert fb["disk_type"] == "pd-balanced", fb


# Machine families and the ONLY boot-disk type each supports for these VMs.
# Next-gen Titanium families (c4/c4d/n4/n4d) support hyperdisk-balanced only;
# older families (c2d/n2/n2d) use pd-balanced. Mismatched disk_type = GCE rejects
# the instance at create time (the exact failure the fallback fleet must avoid).
_FAMILY_DISK_TYPE = {
    "c4d": "hyperdisk-balanced",
    "c4": "hyperdisk-balanced",
    "n4d": "hyperdisk-balanced",
    "n4": "hyperdisk-balanced",
    "c2d": "pd-balanced",
    "n2d": "pd-balanced",
    "n2": "pd-balanced",
}


def _family(machine_type: str) -> str:
    # Longest prefix wins so "n2d"/"c4d" aren't shadowed by "n2"/"c4".
    return max(
        (fam for fam in _FAMILY_DISK_TYPE if machine_type.startswith(fam)),
        key=len,
        default="",
    )


def test_every_fallback_disk_type_matches_its_family_capability():
    """Each fallback's disk_type must be the one its machine family supports."""
    for fb in EncodingWorkerConfig.FALLBACKS:
        fam = _family(fb["machine_type"])
        assert fam, f"unknown family for {fb['machine_type']}"
        assert fb["disk_type"] == _FAMILY_DISK_TYPE[fam], fb


def test_pool_has_at_least_five_types_across_multiple_lineages():
    """The broadened pool must be ≥5 distinct machine types spanning ≥3 lineages
    (AMD Turin / Intel Emerald / AMD Milan / Intel Cascade / AMD Rome) so a
    newest-gen stockout can't exhaust every lane."""
    types = {fb["machine_type"] for fb in EncodingWorkerConfig.FALLBACKS}
    # primary (c4d) + the fallback types.
    types.add(MachineTypes.ENCODING_WORKER)
    assert len(types) >= 5, f"pool has only {len(types)} types: {types}"
    families = {_family(mt) for mt in types}
    assert len(families) >= 3, f"pool spans only {families}"


def test_zone_spread_avoids_same_type_same_zone():
    """No machine type should sit twice in the same zone (correlated stockout)."""
    seen = set()
    for fb in EncodingWorkerConfig.FALLBACKS:
        key = (fb["machine_type"], fb["zone_suffix"])
        assert key not in seen, f"duplicate (type,zone): {key}"
        seen.add(key)


def test_fallback_names_and_ips_are_unique_and_aligned():
    """Names/IPs are zipped by position with FALLBACKS — they must stay aligned
    and unique so no two VMs collide on a resource name or static IP."""
    fb = EncodingWorkerConfig.FALLBACKS
    suffixes = [f["suffix"] for f in fb]
    assert len(set(suffixes)) == len(suffixes), "duplicate fallback suffix"
    assert EncodingWorkerConfig.FALLBACK_VM_NAMES == [
        EncodingWorkerConfig.fallback_vm_name(s) for s in suffixes
    ]
    assert EncodingWorkerConfig.FALLBACK_IP_NAMES == [
        EncodingWorkerConfig.fallback_ip_name(s) for s in suffixes
    ]
    assert len(set(EncodingWorkerConfig.FALLBACK_VM_NAMES)) == len(fb)
    assert len(set(EncodingWorkerConfig.FALLBACK_IP_NAMES)) == len(fb)


def test_original_c4d_fallbacks_unchanged():
    """The first two entries must remain the original c4d a/b fallbacks
    (byte-identical) so `pulumi up` does NOT recreate the existing VMs —
    recreating a c4d VM would need the very capacity we're trying to avoid
    depending on."""
    a, b = EncodingWorkerConfig.FALLBACKS[0], EncodingWorkerConfig.FALLBACKS[1]
    assert a == {"suffix": "a", "zone_suffix": "a",
                 "machine_type": MachineTypes.ENCODING_WORKER, "disk_type": "hyperdisk-balanced"}
    assert b == {"suffix": "b", "zone_suffix": "b",
                 "machine_type": MachineTypes.ENCODING_WORKER, "disk_type": "hyperdisk-balanced"}
