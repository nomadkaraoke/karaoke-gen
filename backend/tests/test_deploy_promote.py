"""Unit tests for the encoding-worker deploy decision logic.

The module lives under infrastructure/ (not an importable package), so we load
it by path. Covers the two things the CI workflow can't test itself: which VMs
to try as a fresh green target, and how to promote a validated green to serving.
"""
import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "infrastructure" / "encoding-worker" / "deploy_promote.py"
)
_spec = importlib.util.spec_from_file_location("deploy_promote", _MODULE_PATH)
deploy_promote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploy_promote)

select_green_candidates = deploy_promote.select_green_candidates
promote_plan = deploy_promote.promote_plan


FALLBACKS = [
    {"vm": "encoding-worker-fallback-a", "zone": "us-central1-a", "ip": "34.55.231.54"},
    {"vm": "encoding-worker-fallback-b", "zone": "us-central1-b", "ip": "35.193.127.24"},
    {"vm": "encoding-worker-fallback-n2c", "zone": "us-central1-c", "ip": "35.226.119.227"},
    {"vm": "encoding-worker-fallback-n2f", "zone": "us-central1-f", "ip": "35.254.152.14"},
]


def _config(**over):
    base = {
        "primary_vm": "encoding-worker-b", "primary_ip": "34.10.189.118",
        "primary_version": "0.192.9",
        "secondary_vm": "encoding-worker-a", "secondary_ip": "34.57.78.246",
        "active_override_vm": "encoding-worker-fallback-n2f",
        "active_override_ip": "35.254.152.14", "active_override_zone": "us-central1-f",
    }
    base.update(over)
    return base


class TestSelectGreenCandidates:
    def test_secondary_first_then_n2_before_c4d_excluding_override(self):
        cands = select_green_candidates(_config(), FALLBACKS)
        vms = [c["vm"] for c in cands]
        # c4d secondary leads; current override (n2f) excluded; n2c (n2) before c4d fallbacks.
        assert vms[0] == "encoding-worker-a"
        assert cands[0]["kind"] == "secondary"
        assert "encoding-worker-fallback-n2f" not in vms  # the serving override is excluded
        assert vms[1] == "encoding-worker-fallback-n2c"    # remaining n2 preferred
        assert vms[1:] == [
            "encoding-worker-fallback-n2c",
            "encoding-worker-fallback-a",
            "encoding-worker-fallback-b",
        ]
        assert all(c["kind"] == "fallback" for c in cands[1:])

    def test_candidate_carries_zone_and_ip(self):
        cands = select_green_candidates(_config(), FALLBACKS)
        n2c = next(c for c in cands if c["vm"] == "encoding-worker-fallback-n2c")
        assert n2c["zone"] == "us-central1-c"
        assert n2c["ip"] == "35.226.119.227"

    def test_no_secondary_still_lists_fallbacks(self):
        cands = select_green_candidates(_config(secondary_vm=None), FALLBACKS)
        assert all(c["kind"] == "fallback" for c in cands)
        assert cands[0]["vm"] == "encoding-worker-fallback-n2c"

    def test_no_override_keeps_all_fallbacks(self):
        cands = select_green_candidates(_config(active_override_vm=None), FALLBACKS)
        fb_vms = [c["vm"] for c in cands if c["kind"] == "fallback"]
        # n2 fallbacks first (either order among the two n2s is fine — assert as a set head)
        assert set(fb_vms[:2]) == {"encoding-worker-fallback-n2c", "encoding-worker-fallback-n2f"}
        assert set(fb_vms) == {f["vm"] for f in FALLBACKS}


class TestPromotePlanFallbackBranch:
    def test_fallback_green_becomes_override_and_drains_old(self):
        green = {"vm": "encoding-worker-fallback-n2c", "ip": "35.226.119.227",
                 "zone": "us-central1-c", "kind": "fallback"}
        plan = promote_plan(green, _config(), now="NOW", version="0.194.1")
        u = plan["firestore_updates"]
        assert u["active_override_vm"] == "encoding-worker-fallback-n2c"
        assert u["active_override_ip"] == "35.226.119.227"
        assert u["active_override_zone"] == "us-central1-c"
        assert u["active_override_version"] == "0.194.1"
        assert u["deploy_in_progress"] is False
        # primary/secondary untouched (c4d resumes when capacity returns)
        assert "primary_vm" not in u and "secondary_vm" not in u
        # old override (n2f) drained + stopped
        assert plan["drain_and_stop"] == [
            {"vm": "encoding-worker-fallback-n2f", "ip": "35.254.152.14", "zone": "us-central1-f"}
        ]

    def test_no_drain_when_no_previous_override(self):
        green = {"vm": "encoding-worker-fallback-n2c", "ip": "35.226.119.227",
                 "zone": "us-central1-c", "kind": "fallback"}
        plan = promote_plan(green, _config(active_override_vm=None), now="NOW", version="0.194.1")
        assert plan["drain_and_stop"] == []

    def test_no_self_drain_when_green_is_current_override(self):
        # Degenerate (shouldn't happen since candidates exclude the override), but
        # must never drain-stop the worker we just promoted.
        green = {"vm": "encoding-worker-fallback-n2f", "ip": "35.254.152.14",
                 "zone": "us-central1-f", "kind": "fallback"}
        plan = promote_plan(green, _config(), now="NOW", version="0.194.1")
        assert plan["drain_and_stop"] == []


class TestPromotePlanSecondaryBranch:
    def test_secondary_green_swaps_and_clears_stale_override(self):
        green = {"vm": "encoding-worker-a", "ip": "34.57.78.246",
                 "zone": "us-central1-c", "kind": "secondary"}
        plan = promote_plan(green, _config(), now="NOW", version="0.194.1")
        u = plan["firestore_updates"]
        # green -> primary, old primary -> secondary
        assert u["primary_vm"] == "encoding-worker-a"
        assert u["primary_version"] == "0.194.1"
        assert u["secondary_vm"] == "encoding-worker-b"
        assert u["secondary_version"] == "0.192.9"
        # stale override cleared so active_url follows the fresh primary
        assert u["active_override_vm"] is None
        assert u["active_override_version"] is None
        assert u["deploy_in_progress"] is False
        # both the old primary and the old override get drained + stopped
        drained = {d["vm"] for d in plan["drain_and_stop"]}
        assert drained == {"encoding-worker-b", "encoding-worker-fallback-n2f"}

    def test_secondary_never_drains_the_promoted_green(self):
        # Pathological config where a stale override (or primary) names the green
        # VM — the plan must never tell the workflow to stop what it just promoted.
        green = {"vm": "encoding-worker-a", "ip": "34.57.78.246",
                 "zone": "us-central1-c", "kind": "secondary"}
        cfg = _config(active_override_vm="encoding-worker-a",
                      active_override_ip="34.57.78.246",
                      active_override_zone="us-central1-c")
        plan = promote_plan(green, cfg, now="NOW", version="0.194.2")
        assert all(d["vm"] != "encoding-worker-a" for d in plan["drain_and_stop"])
        # The override is still cleared in config even though we don't stop that VM.
        assert plan["firestore_updates"]["active_override_vm"] is None

    def test_secondary_swap_without_override(self):
        green = {"vm": "encoding-worker-a", "ip": "34.57.78.246",
                 "zone": "us-central1-c", "kind": "secondary"}
        plan = promote_plan(green, _config(active_override_vm=None), now="NOW", version="0.194.1")
        u = plan["firestore_updates"]
        assert "active_override_vm" not in u  # nothing to clear
        assert [d["vm"] for d in plan["drain_and_stop"]] == ["encoding-worker-b"]
