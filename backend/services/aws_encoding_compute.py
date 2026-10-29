"""EC2 adapter for the AWS encoding worker.

The AWS worker (an EC2 Spot instance running the same ``gce_encoding`` HTTP
service) is just another candidate in the encoding-worker pool. Rather than
teach every caller about EC2, this module mimics the slice of
``google.cloud.compute_v1.InstancesClient`` that ``EncodingWorkerManager`` uses
(``get`` / ``start`` / ``stop``), so the manager routes AWS VMs here and the
GCE code paths stay untouched:

- EC2 states map to GCE statuses (``running`` → ``RUNNING``, ``stopped`` →
  ``TERMINATED``, …) so the existing RUNNING/STAGING/stopped checks just work.
- ``start`` returns an operation object shaped like a compute ExtendedOperation
  (``result()``, ``error_code``, ``error_message``); EC2 capacity errors surface
  as codes in ``CAPACITY_ERROR_CODES`` so stockout cooldowns apply unchanged.
- A missing instance raises ``google.api_core.exceptions.NotFound``, which the
  manager already treats as "skip this candidate".

Credentials: an IAM user limited to Start/Stop/Describe on instances tagged
``Project=nomad-encoding``, stored as JSON ``{"access_key_id", "secret_access_key"}``
in Secret Manager ``aws-encoding-controller-credentials`` (env
``AWS_ENCODING_CONTROLLER_CREDENTIALS`` overrides, for tests/local runs).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from google.api_core import exceptions as gcp_exceptions

logger = logging.getLogger(__name__)

CREDENTIALS_SECRET = "aws-encoding-controller-credentials"
CREDENTIALS_ENV = "AWS_ENCODING_CONTROLLER_CREDENTIALS"

# EC2 instance state → GCE instance status.
_EC2_TO_GCE_STATUS = {
    "pending": "STAGING",
    "running": "RUNNING",
    "stopping": "STOPPING",
    "stopped": "TERMINATED",
    "shutting-down": "STOPPING",
    "terminated": "TERMINATED",
}


@dataclass(frozen=True)
class AwsVm:
    """Where an AWS worker lives. Built from a fallback-pool entry."""

    instance_id: str
    region: str


@dataclass
class _Instance:
    status: str


class AwsStartOperation:
    """Duck-types a compute ExtendedOperation for ``_wait_for_compute_operation``."""

    def __init__(self, run):
        self._run = run
        self.error_code = ""
        self.error_message = ""

    def result(self, timeout: Optional[float] = None) -> None:
        from botocore.exceptions import ClientError

        try:
            self._run()
        except ClientError as e:
            err = e.response.get("Error", {})
            self.error_code = err.get("Code", "") or "ClientError"
            self.error_message = err.get("Message", "") or str(e)


def _load_credentials(project_id: str) -> Dict[str, str]:
    raw = os.environ.get(CREDENTIALS_ENV)
    if not raw:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{CREDENTIALS_SECRET}/versions/latest"
        raw = client.access_secret_version(name=name).payload.data.decode("utf-8")
    creds = json.loads(raw)
    return {"access_key_id": creds["access_key_id"], "secret_access_key": creds["secret_access_key"]}


class AwsComputeAdapter:
    """``get``/``start``/``stop`` for registered AWS worker VMs, by VM name."""

    def __init__(self, vms: Dict[str, AwsVm], project_id: str = "nomadkaraoke", client_factory=None):
        self._vms = dict(vms)
        self._project_id = project_id
        self._client_factory = client_factory
        self._clients: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def handles(self, vm_name: str) -> bool:
        return vm_name in self._vms

    def _client(self, region: str):
        with self._lock:
            if region not in self._clients:
                if self._client_factory is not None:
                    self._clients[region] = self._client_factory(region)
                else:
                    import boto3

                    creds = _load_credentials(self._project_id)
                    self._clients[region] = boto3.client(
                        "ec2",
                        region_name=region,
                        aws_access_key_id=creds["access_key_id"],
                        aws_secret_access_key=creds["secret_access_key"],
                    )
            return self._clients[region]

    def _vm(self, instance: str) -> AwsVm:
        vm = self._vms.get(instance)
        if vm is None:
            raise gcp_exceptions.NotFound(f"AWS worker {instance} is not registered")
        return vm

    def get(self, project: str = "", zone: str = "", instance: str = "") -> _Instance:
        from botocore.exceptions import ClientError

        vm = self._vm(instance)
        try:
            resp = self._client(vm.region).describe_instances(InstanceIds=[vm.instance_id])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code", "").startswith("InvalidInstanceID"):
                raise gcp_exceptions.NotFound(f"AWS worker {instance} ({vm.instance_id}) not found") from e
            raise
        reservations = resp.get("Reservations") or []
        if not reservations or not reservations[0].get("Instances"):
            raise gcp_exceptions.NotFound(f"AWS worker {instance} ({vm.instance_id}) not found")
        state = reservations[0]["Instances"][0]["State"]["Name"]
        return _Instance(status=_EC2_TO_GCE_STATUS.get(state, state.upper()))

    def start(self, project: str = "", zone: str = "", instance: str = "") -> AwsStartOperation:
        vm = self._vm(instance)
        logger.info("Starting AWS worker %s (%s in %s)", instance, vm.instance_id, vm.region)
        return AwsStartOperation(lambda: self._client(vm.region).start_instances(InstanceIds=[vm.instance_id]))

    def stop(self, project: str = "", zone: str = "", instance: str = "") -> None:
        vm = self._vm(instance)
        logger.info("Stopping AWS worker %s (%s in %s)", instance, vm.instance_id, vm.region)
        self._client(vm.region).stop_instances(InstanceIds=[vm.instance_id])


def aws_vms_from_pool(entries) -> Dict[str, AwsVm]:
    """``{vm_name: AwsVm}`` for the ``provider == "aws"`` entries of a fallback pool."""
    vms: Dict[str, AwsVm] = {}
    for e in entries or []:
        if (e.get("provider") or "").lower() != "aws":
            continue
        name = e.get("vm") or e.get("vm_name")
        if name and e.get("instance_id") and e.get("region"):
            vms[name] = AwsVm(instance_id=e["instance_id"], region=e["region"])
    return vms
