"""Unit tests for the EC2 adapter behind the AWS encoding worker.

The adapter must look exactly like the compute_v1.InstancesClient slice the
manager uses, so the manager's GCE-shaped status checks, capacity cooldowns
and NotFound skips apply to the AWS worker unchanged.
"""
import json

import boto3
import pytest
from botocore.stub import Stubber
from google.api_core import exceptions as gcp_exceptions

from backend.services.aws_encoding_compute import (
    AwsComputeAdapter,
    AwsVm,
    _load_credentials,
    aws_vms_from_pool,
)
from backend.services.encoding_errors import CAPACITY_ERROR_CODES

VM = "aws-encoding-worker-1"
IID = "i-0123456789abcdef0"


@pytest.fixture
def ec2():
    client = boto3.client("ec2", region_name="us-east-2", aws_access_key_id="x", aws_secret_access_key="y")
    with Stubber(client) as stub:
        yield client, stub


def _adapter(client):
    return AwsComputeAdapter({VM: AwsVm(IID, "us-east-2")}, client_factory=lambda region: client)


def _describe(state):
    return {"Reservations": [{"Instances": [{"InstanceId": IID, "State": {"Name": state, "Code": 0}}]}]}


@pytest.mark.parametrize("ec2_state,gce_status", [
    ("running", "RUNNING"),
    ("pending", "STAGING"),
    ("stopping", "STOPPING"),
    ("stopped", "TERMINATED"),
    ("shutting-down", "STOPPING"),
])
def test_get_maps_ec2_state_to_gce_status(ec2, ec2_state, gce_status):
    client, stub = ec2
    stub.add_response("describe_instances", _describe(ec2_state), {"InstanceIds": [IID]})
    assert _adapter(client).get(project="p", zone="us-east-2b", instance=VM).status == gce_status


def test_get_missing_instance_raises_not_found(ec2):
    client, stub = ec2
    stub.add_client_error("describe_instances", service_error_code="InvalidInstanceID.NotFound")
    with pytest.raises(gcp_exceptions.NotFound):
        _adapter(client).get(instance=VM)


def test_unregistered_vm_raises_not_found(ec2):
    client, _ = ec2
    with pytest.raises(gcp_exceptions.NotFound):
        _adapter(client).get(instance="encoding-worker-a")


def test_start_success_leaves_no_error_code(ec2):
    client, stub = ec2
    stub.add_response("start_instances", {"StartingInstances": []}, {"InstanceIds": [IID]})
    op = _adapter(client).start(instance=VM)
    op.result(timeout=30)
    assert op.error_code == ""


def test_start_capacity_error_is_a_capacity_code(ec2):
    client, stub = ec2
    stub.add_client_error(
        "start_instances", service_error_code="InsufficientInstanceCapacity",
        service_message="no c7a.8xlarge capacity in us-east-2b",
    )
    op = _adapter(client).start(instance=VM)
    op.result()
    assert op.error_code == "InsufficientInstanceCapacity"
    assert op.error_code in CAPACITY_ERROR_CODES
    assert "capacity" in op.error_message


def test_stop_calls_stop_instances(ec2):
    client, stub = ec2
    stub.add_response("stop_instances", {"StoppingInstances": []}, {"InstanceIds": [IID]})
    _adapter(client).stop(instance=VM)
    stub.assert_no_pending_responses()


def test_handles_only_registered_vms():
    adapter = AwsComputeAdapter({VM: AwsVm(IID, "us-east-2")}, client_factory=lambda r: None)
    assert adapter.handles(VM)
    assert not adapter.handles("encoding-worker-a")


def test_aws_vms_from_pool_keeps_only_complete_aws_entries():
    pool = [
        {"vm": "encoding-worker-fallback-c2df", "zone": "us-central1-f", "ip": "1.2.3.4"},
        {"vm": VM, "zone": "us-east-2b", "ip": "5.6.7.8", "provider": "aws", "instance_id": IID, "region": "us-east-2"},
        {"vm": "aws-incomplete", "zone": "us-east-2b", "ip": "9.9.9.9", "provider": "aws"},
    ]
    assert aws_vms_from_pool(pool) == {VM: AwsVm(IID, "us-east-2")}


def test_credentials_env_override(monkeypatch):
    monkeypatch.setenv(
        "AWS_ENCODING_CONTROLLER_CREDENTIALS",
        json.dumps({"access_key_id": "AKIA", "secret_access_key": "s3cr3t", "extra": 1}),
    )
    assert _load_credentials("nomadkaraoke") == {"access_key_id": "AKIA", "secret_access_key": "s3cr3t"}
