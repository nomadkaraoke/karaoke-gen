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
