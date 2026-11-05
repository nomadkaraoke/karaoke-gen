"""EncodingWorkerManager once the GCE c4d blue-green pair is retired (2026-11).

config/encoding-worker then has empty (or absent) primary_vm/secondary_vm, and the
pool is just the fallbacks: the AWS worker (preferred) + the GCE c2df VM. Every
path must route to a fallback and record it as the active override, never to a
blank primary.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.encoding_errors import EncodingWorkerStartError
from backend.services.encoding_worker_manager import (
    EncodingWorkerCandidate,
    EncodingWorkerManager,
)

AWS_ENTRY = {
    "vm": "aws-encoding-worker-1", "zone": "us-east-2b", "ip": "3.3.3.3",
    "machine_type": "c7a-4xlarge", "provider": "aws", "instance_id": "i-0abc", "region": "us-east-2",
}
C2D_ENTRY = {"vm": "encoding-worker-fallback-c2df", "zone": "us-central1-f", "ip": "2.2.2.2",
             "machine_type": "c2d-highcpu-16"}

NO_PAIR = {"primary_vm": "", "primary_ip": "", "primary_version": "0.291.0",
           "secondary_vm": "", "secondary_ip": ""}


def _db(**config):
    data = {"last_activity_at": None, "capacity_state": {}, "speed_rank_overrides": {"c7a": 5},
            **config}
    db = MagicMock()
    snap = MagicMock(exists=True)
    snap.to_dict.return_value = data
    doc = MagicMock()
    doc.get.return_value = snap
    db.collection.return_value.document.return_value = doc
    return db, doc


def _manager(db, entries=(C2D_ENTRY, AWS_ENTRY)):
    gce = MagicMock(name="gce")
    aws = MagicMock(name="aws")
    aws.handles.side_effect = lambda vm: vm == AWS_ENTRY["vm"]
    m = EncodingWorkerManager(db=db, compute_client=gce,
                              fallback_vms_raw=json.dumps(list(entries)), aws_compute=aws)
    return m, gce, aws


def test_get_config_tolerates_absent_pair_keys():
    db, _ = _db()  # no primary/secondary keys at all
    m, _, _ = _manager(db)
    config = m.get_config()
    assert config.primary_vm == "" and config.secondary_vm == ""
    assert not config.has_primary
    assert config.primary_url == ""
    assert config.active_url == ""


def test_active_url_follows_override_without_primary():
    db, _ = _db(**NO_PAIR, active_override_vm="aws-encoding-worker-1", active_override_ip="3.3.3.3")
    m, _, _ = _manager(db)
    assert m.get_config().active_url == "http://3.3.3.3:8080"


def test_ranked_candidates_skip_blank_primary():
    db, _ = _db(**NO_PAIR)
    m, _, _ = _manager(db)
    ranked = m.ranked_candidates()
    assert [c.vm_name for c in ranked] == ["aws-encoding-worker-1", "encoding-worker-fallback-c2df"]
    assert all(c.is_primary is False for c in ranked)


def test_aws_landing_sets_override_not_clears_it():
    """With no primary, index 0 (AWS) is a fallback: starting it must SET the
    override (the legacy 'index 0 is the primary' rule would clear it, leaving
    active_url empty)."""
    db, doc = _db(**NO_PAIR)
    m, _, aws = _manager(db)
    aws.get.return_value = MagicMock(status="RUNNING")
    m.update_activity = MagicMock()
    result = m.ensure_any_running(m.ranked_candidates())
    assert result["vm_name"] == "aws-encoding-worker-1"
    assert result["fell_back"] is True
    update = doc.update.call_args[0][0]
    assert update["active_override_vm"] == "aws-encoding-worker-1"
    assert update["active_override_provider"] == "aws"


def test_hand_built_candidates_keep_legacy_index0_rule():
    """Candidates built without is_primary (None) keep the old position rule."""
    db, doc = _db(**NO_PAIR)
    m, gce, _ = _manager(db)
    gce.get.return_value = MagicMock(status="RUNNING")
    m.update_activity = MagicMock()
    m._clear_active_override = MagicMock()
    m._set_active_override = MagicMock()
    result = m.ensure_any_running([EncodingWorkerCandidate("vm-x", "us-central1-f", "9.9.9.9")])
    assert result["fell_back"] is False
    m._clear_active_override.assert_called_once()
    m._set_active_override.assert_not_called()


def test_ensure_primary_running_without_pair_starts_ranked_candidate():
    db, _ = _db(**NO_PAIR)
    m, gce, _ = _manager(db)
    m.ensure_any_running = MagicMock(return_value={"started": True, "vm_name": "aws-encoding-worker-1",
                                                   "fell_back": True})
    result = m.ensure_primary_running()
    assert result["vm_name"] == "aws-encoding-worker-1"
    (candidates,), _ = m.ensure_any_running.call_args
    assert candidates[0].vm_name == "aws-encoding-worker-1"
    gce.get.assert_not_called()  # never looks up a blank primary on GCE


def test_ensure_preferred_running_without_pair_never_takes_primary_path():
    # No rank override → c2df ranks first; still no primary path.
    db, _ = _db(**NO_PAIR, speed_rank_overrides={})
    m, _, _ = _manager(db)
    m.ensure_any_running = MagicMock(return_value={"started": False})
    m.ensure_preferred_running()
    m.ensure_any_running.assert_called_once()


@pytest.mark.parametrize("method", ["ensure_preferred_running", "ensure_primary_running"])
def test_no_candidates_at_all_raises_start_error(method):
    db, _ = _db(**NO_PAIR)
    m, _, _ = _manager(db, entries=())
    with pytest.raises(EncodingWorkerStartError) as ei:
        getattr(m, method)()
    assert ei.value.code == "NO_CANDIDATES"


def test_demote_aws_without_primary_retries_on_another_worker():
    """A broken AWS worker is cooled down (via its machine_type) and the override
    cleared, so the retry's URL resolves to the GCE fallback instead."""
    from backend.services.encoding_service import EncodingService

    db, doc = _db(**NO_PAIR, active_override_vm="aws-encoding-worker-1",
                  active_override_zone="us-east-2b", active_override_ip="3.3.3.3")
    m, _, _ = _manager(db)
    m.demote_active_worker("metadata unreachable")
    state = doc.set.call_args[0][0]["capacity_state"]
    assert list(state) == ["c7a-4xlarge@us-east-2b"]
    assert doc.update.call_args[0][0]["active_override_vm"] is None

    # Next read: override gone, AWS in cooldown → URL is c2df.
    db2, _ = _db(**NO_PAIR, capacity_state=state)
    m2, _, _ = _manager(db2)
    svc = EncodingService.__new__(EncodingService)
    svc._cached_url = None
    svc._url_cached_at = 0.0
    svc._worker_manager = m2
    assert svc._get_worker_url() == "http://2.2.2.2:8080"


def test_demote_with_nothing_serving_is_quiet(caplog):
    db, _ = _db(**NO_PAIR)
    m, _, _ = _manager(db)
    m.demote_active_worker("x")
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_encoding_service_resolves_top_candidate_when_no_url():
    from backend.services.encoding_service import EncodingService

    db, _ = _db(**NO_PAIR)
    m, _, _ = _manager(db)
    svc = EncodingService.__new__(EncodingService)
    svc._cached_url = None
    svc._url_cached_at = 0.0
    svc._worker_manager = m
    assert svc._get_worker_url() == "http://3.3.3.3:8080"


@pytest.mark.asyncio
async def test_single_candidate_warmup_invalidates_cache_on_fallback():
    from backend.services.encoding_service import EncodingService

    svc = EncodingService.__new__(EncodingService)
    svc._cached_url = "http://stale:8080"
    svc._url_cached_at = 1e12
    manager = MagicMock()
    manager.get_config.return_value = MagicMock()
    manager.ranked_candidates.return_value = [
        EncodingWorkerCandidate("aws-encoding-worker-1", "us-east-2b", "3.3.3.3", is_primary=False)
    ]
    manager.ensure_primary_running.return_value = {
        "started": False, "vm_name": "aws-encoding-worker-1", "zone": "us-east-2b",
        "primary_url": "http://3.3.3.3:8080", "fell_back": True,
    }
    manager.wait_for_worker_ready = AsyncMock()
    svc._worker_manager = manager
    await svc._warmup_encoding_worker_fallback("job1")
    assert svc._cached_url is None
