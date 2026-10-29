"""EncodingWorkerManager with the AWS encoding worker in the pool.

Covers routing AWS VMs to the EC2 adapter (GCE untouched), the shared fallback
parser passing provider fields through, ranking with Firestore
speed_rank_overrides, the ranked warmup, and the override's provider tag.
"""
import json
from unittest.mock import MagicMock

import pytest

from backend.services.aws_encoding_compute import AwsStartOperation
from backend.services.encoding_errors import EncodingWorkerCapacityError
from backend.services.encoding_worker_manager import EncodingWorkerManager, parse_fallback_vms

AWS_ENTRY = {
    "vm": "aws-encoding-worker-1", "zone": "us-east-2b", "ip": "3.3.3.3",
    "machine_type": "c7a-8xlarge", "provider": "aws", "instance_id": "i-0abc", "region": "us-east-2",
}
C2D_ENTRY = {"vm": "encoding-worker-fallback-c2df", "zone": "us-central1-f", "ip": "2.2.2.2",
             "machine_type": "c2d-highcpu-16"}


def _db(**config):
    data = {
        "primary_vm": "encoding-worker-a", "primary_ip": "1.1.1.1", "primary_version": "1.0",
        "secondary_vm": "encoding-worker-b", "secondary_ip": "1.1.1.2",
        "last_activity_at": None, "capacity_state": {}, **config,
    }
    db = MagicMock()
    snap = MagicMock(exists=True)
    snap.to_dict.return_value = data
    doc = MagicMock()
    doc.get.return_value = snap
    db.collection.return_value.document.return_value = doc
    return db, doc


def _manager(db, aws=None, entries=(C2D_ENTRY, AWS_ENTRY)):
    gce = MagicMock(name="gce")
    aws = aws or MagicMock(name="aws")
    aws.handles.side_effect = lambda vm: vm == AWS_ENTRY["vm"]
    m = EncodingWorkerManager(db=db, compute_client=gce, fallback_vms_raw=json.dumps(list(entries)), aws_compute=aws)
    return m, gce, aws


def test_parse_fallback_vms_passes_aws_fields_through():
    parsed = parse_fallback_vms(json.dumps([C2D_ENTRY, AWS_ENTRY]))
    assert parsed[0] == {**C2D_ENTRY, "kind": "fallback"}
    assert parsed[1]["provider"] == "aws"
    assert parsed[1]["instance_id"] == "i-0abc" and parsed[1]["region"] == "us-east-2"


@pytest.mark.parametrize("raw", [None, "", "not json", "{}", "null"])
def test_parse_fallback_vms_never_raises(raw):
    assert parse_fallback_vms(raw) == []


def test_aws_vm_ops_go_to_adapter_and_gce_untouched():
    db, _ = _db()
    m, gce, aws = _manager(db)
    aws.get.return_value = MagicMock(status="RUNNING")
    gce.get.return_value = MagicMock(status="TERMINATED")

    assert m.get_vm_status("aws-encoding-worker-1", zone="us-east-2b") == "RUNNING"
    assert m.get_vm_status("encoding-worker-a") == "TERMINATED"
    aws.get.assert_called_once()
    gce.get.assert_called_once()


def test_aws_capacity_error_raises_typed_capacity_error():
    db, _ = _db()
    m, _, aws = _manager(db)
    aws.get.return_value = MagicMock(status="TERMINATED")

    def _no_capacity():
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "InsufficientInstanceCapacity", "Message": "none"}}, "StartInstances")

    aws.start.return_value = AwsStartOperation(_no_capacity)
    with pytest.raises(EncodingWorkerCapacityError):
        m.start_vm("aws-encoding-worker-1", zone="us-east-2b")


def test_ranked_candidates_default_keeps_gce_primary_first_and_aws_last():
    db, _ = _db()
    m, _, _ = _manager(db)
    ranked = m.ranked_candidates()
    assert [c.vm_name for c in ranked] == ["encoding-worker-a", "encoding-worker-fallback-c2df", "aws-encoding-worker-1"]
    assert ranked[0].is_primary and ranked[-1].provider == "aws"


def test_ranked_candidates_honour_speed_rank_overrides():
    db, _ = _db(speed_rank_overrides={"c7a": 5})
    m, _, _ = _manager(db)
    assert m.ranked_candidates()[0].vm_name == "aws-encoding-worker-1"


def test_ensure_preferred_running_is_primary_path_when_primary_ranks_first():
    db, _ = _db()
    m, _, _ = _manager(db)
    m.ensure_primary_running = MagicMock(return_value={"started": False})
    m.ensure_any_running = MagicMock()
    m.ensure_preferred_running()
    m.ensure_primary_running.assert_called_once()
    m.ensure_any_running.assert_not_called()


def test_ensure_preferred_running_starts_aws_when_preferred():
    db, _ = _db(speed_rank_overrides={"c7a": 5})
    m, _, _ = _manager(db)
    m.ensure_primary_running = MagicMock()
    m.ensure_any_running = MagicMock(return_value={"started": True, "vm_name": "aws-encoding-worker-1"})
    m.ensure_preferred_running()
    m.ensure_primary_running.assert_not_called()
    (candidates,), _ = m.ensure_any_running.call_args
    assert candidates[0].vm_name == "aws-encoding-worker-1"


def test_active_override_records_provider():
    db, doc = _db()
    m, _, _ = _manager(db)
    aws_candidate = next(c for c in m.ranked_candidates() if c.provider == "aws")
    m._set_active_override(aws_candidate)
    update = doc.update.call_args[0][0]
    assert update["active_override_vm"] == "aws-encoding-worker-1"
    assert update["active_override_provider"] == "aws"

    db2, doc2 = _db(active_override_vm="aws-encoding-worker-1", active_override_provider="aws")
    m2, _, _ = _manager(db2)
    m2._clear_active_override()
    assert doc2.update.call_args[0][0]["active_override_provider"] is None
