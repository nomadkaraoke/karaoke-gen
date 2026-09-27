"""Invariants for the encoding-worker capacity-fallback fleet config.

Guards the machine-family diversification added 2026-08-12 after a region-wide
c4d-highcpu-32 ZONE_RESOURCE_POOL_EXHAUSTED stockout (us-central1-a/-b/-c at once)
exhausted every same-family lane and forced slow local encoding (→ 524 on
preview, parked renders).

Run locally with: `pytest infrastructure/test_encoding_worker_config.py`
(infrastructure is not part of the CI backend test gate — Pulumi validates the
resource graph via `pulumi preview`).
"""

from config import ENCODING_WORKER_ZONE, EncodingWorkerConfig, MachineTypes


def test_fallback_is_a_different_machine_family_and_zone_from_primary():
    """The on-demand fallback must not share the Spot pair's family or zone,
    otherwise a single c4d / Spot / zone stockout takes out every lane at once
    (incident 2026-08-12)."""
    assert EncodingWorkerConfig.FALLBACKS, "need at least one capacity fallback"
    primary_family = _family(MachineTypes.ENCODING_WORKER)
    primary_zone_suffix = ENCODING_WORKER_ZONE.rsplit("-", 1)[1]
    for fb in EncodingWorkerConfig.FALLBACKS:
        assert _family(fb["machine_type"]) != primary_family, fb
        assert fb["zone_suffix"] != primary_zone_suffix, fb


def test_fleet_is_cost_cut_shape():
    """2026-09-26 cost cut: Spot a/b pair + exactly one on-demand fallback, all
    16 vCPU. Growing this again should be a deliberate decision (update the
    fallback secret + docs), not an accident."""
    assert len(EncodingWorkerConfig.VM_NAMES) == 2
    assert len(EncodingWorkerConfig.FALLBACKS) == 1
    assert EncodingWorkerConfig.PRIMARY_PAIR_SPOT is True
    for mt in [MachineTypes.ENCODING_WORKER] + [fb["machine_type"] for fb in EncodingWorkerConfig.FALLBACKS]:
        assert mt.endswith("-16"), mt


def test_idle_shutdown_is_fast_but_outlives_review_heartbeat():
    """Idle VMs stop after 5 min, checked every 2 min. The lyrics-review page
    heartbeats every 2 min (frontend REVIEW_HEARTBEAT_INTERVAL_MS) so an active
    review keeps the warm VM alive — the timeout must leave margin over it."""
    assert EncodingWorkerConfig.IDLE_TIMEOUT_MINUTES == 5
    assert EncodingWorkerConfig.IDLE_CHECK_SCHEDULE == "*/2 * * * *"
    review_heartbeat_minutes = 2
    assert EncodingWorkerConfig.IDLE_TIMEOUT_MINUTES >= 2 * review_heartbeat_minutes


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


def test_worker_boot_disk_fits_packer_image_size():
    """GCE cannot create a boot disk smaller than its source image's disk size.
    The Packer image's `disk_size` must stay <= DiskSizes.ENCODING_WORKER, or
    every worker (re)creation from the new image fails. Also keeps the
    ENCODING_WORKER_IMAGE mirror constant honest."""
    import re
    from pathlib import Path

    from config import DiskSizes

    hcl = (Path(__file__).parent / "packer" / "encoding-worker.pkr.hcl").read_text()
    match = re.search(r"^\s*disk_size\s*=\s*(\d+)", hcl, re.MULTILINE)
    assert match, "disk_size not found in encoding-worker.pkr.hcl"
    image_size = int(match.group(1))
    assert image_size == DiskSizes.ENCODING_WORKER_IMAGE
    assert image_size <= DiskSizes.ENCODING_WORKER
    # Headroom floor: OS + baked venv is ~12-15 GB and per-job scratch lives in
    # /tmp on the boot disk. Don't let a future "cost cut" starve encodes.
    assert DiskSizes.ENCODING_WORKER >= 40
