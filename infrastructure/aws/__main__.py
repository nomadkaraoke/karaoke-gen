"""Nomad Karaoke AWS account: the EC2 encoding worker.

Paid for by the AWS Activate credits ($5k, acct 790139457751, exp 2028-10-31).
The worker runs the same ``gce_encoding`` HTTP service as the GCE encoding VMs
and is one more candidate in the backend's encoding-worker pool (provider
"aws"). Plan: karaoke-gen docs/archive/2026-10-29-aws-encoding-worker-plan.md.

- On-demand instance (Spot capacity was too thin — see INSTANCE_TYPE), ``stop``
  on OS shutdown (the idle watchdog), so the EBS root (venv, caches) survives
  like a GCE boot disk.
- Elastic IP + port 8080 (API-key auth, same as the GCE firewall).
- GCP access via Workload Identity Federation as encoding-worker@ (the
  ``nomad-encoding-worker`` role is the federated identity; see
  infrastructure/modules/aws_encoding_wif.py in the GCP stack).
- ``nomad-encoding-controller`` IAM user: the backend's start/stop/describe
  rights, scoped by tag. Its access key is created by hand and stored in GCP
  Secret Manager ``aws-encoding-controller-credentials`` (see README).
"""

import json
from pathlib import Path

import pulumi
import pulumi_aws as aws

PROJECT_TAG = {"Project": "nomad-encoding"}
VM_NAME = "aws-encoding-worker-1"
# On-demand, not Spot: Spot placement scores for c7a/c7i/c6a/c8a 4–8xlarge were
# 1-3/10 across us-east-1/2 on 2026-10-29 (first create failed with
# InsufficientInstanceCapacity). A stop/start-on-demand worker must win
# capacity on every start, and an interruption mid-job is costly, so we pay
# on-demand from credits: ~$0.82/h x ~100 h/mo ≈ $82/mo of the ~$212/mo.
# 16 vCPU / 32 GB matched or beat GCE c4d-highcpu-16 in the replay benchmark.
INSTANCE_TYPE = "c7a.4xlarge"
AZ = "us-east-2b"
WIF_PROVIDER = (
    "projects/718638054799/locations/global/workloadIdentityPools/"
    "aws-encoding/providers/aws-790139457751"
)

# --- Worker identity (federated into GCP; also SSM for operator shell) ----------
role = aws.iam.Role(
    "worker-role",
    name="nomad-encoding-worker",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }),
    tags=PROJECT_TAG,
)
aws.iam.RolePolicyAttachment(
    "worker-ssm",
    role=role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)
profile = aws.iam.InstanceProfile("worker-profile", name="nomad-encoding-worker", role=role.name)

# --- Backend controller (start/stop/describe the tagged worker) -----------------
controller = aws.iam.User("controller", name="nomad-encoding-controller", tags=PROJECT_TAG)
aws.iam.UserPolicy(
    "controller-policy",
    user=controller.name,
    policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["ec2:StartInstances", "ec2:StopInstances"],
                "Resource": "arn:aws:ec2:*:790139457751:instance/*",
                "Condition": {"StringEquals": {"aws:ResourceTag/Project": "nomad-encoding"}},
            },
            {"Effect": "Allow", "Action": ["ec2:DescribeInstances"], "Resource": "*"},
        ],
    }),
)

# --- Network ---------------------------------------------------------------------
vpc = aws.ec2.get_vpc(default=True)
subnet = aws.ec2.get_subnet(filters=[
    aws.ec2.GetSubnetFilterArgs(name="vpc-id", values=[vpc.id]),
    aws.ec2.GetSubnetFilterArgs(name="availability-zone", values=[AZ]),
    aws.ec2.GetSubnetFilterArgs(name="default-for-az", values=["true"]),
])
sg = aws.ec2.SecurityGroup(
    "worker-sg",
    name="nomad-encoding-worker",
    description="Encoding worker HTTP (API-key auth), same as the GCE encoding-worker firewall",
    vpc_id=vpc.id,
    ingress=[aws.ec2.SecurityGroupIngressArgs(protocol="tcp", from_port=8080, to_port=8080, cidr_blocks=["0.0.0.0/0"])],
    egress=[aws.ec2.SecurityGroupEgressArgs(protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"])],
    tags=PROJECT_TAG,
)

# --- Instance --------------------------------------------------------------------
ami = aws.ssm.get_parameter(name="/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id")
provision = Path(__file__).parent.parent.joinpath("encoding-worker", "aws", "provision.sh").read_text()
user_data = provision.replace(
    "set -euxo pipefail\n",
    f"set -euxo pipefail\nexport WIF_PROVIDER='{WIF_PROVIDER}' VM_NAME='{VM_NAME}'\n",
    1,
)

instance = aws.ec2.Instance(
    "worker",
    ami=ami.value,
    instance_type=INSTANCE_TYPE,
    subnet_id=subnet.id,
    vpc_security_group_ids=[sg.id],
    iam_instance_profile=profile.name,
    user_data=user_data,
    instance_initiated_shutdown_behavior="stop",
    metadata_options=aws.ec2.InstanceMetadataOptionsArgs(http_tokens="required", http_put_response_hop_limit=2),
    root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
        volume_size=100, volume_type="gp3", iops=6000, throughput=400, delete_on_termination=True,
        tags={**PROJECT_TAG, "Name": VM_NAME},
    ),
    tags={**PROJECT_TAG, "Name": VM_NAME},
    # The root volume is the worker's state (like a GCE boot disk): a newer
    # Ubuntu AMI or an edited provision.sh must never silently replace it.
    opts=pulumi.ResourceOptions(ignore_changes=["ami", "user_data"]),
)
eip = aws.ec2.Eip("worker-eip", domain="vpc", tags={**PROJECT_TAG, "Name": VM_NAME})
aws.ec2.EipAssociation("worker-eip-assoc", instance_id=instance.id, allocation_id=eip.id)

pulumi.export("vm_name", VM_NAME)
pulumi.export("instance_id", instance.id)
pulumi.export("public_ip", eip.public_ip)
pulumi.export("availability_zone", instance.availability_zone)
pulumi.export(
    "fallback_vms_entry",
    pulumi.Output.all(instance.id, eip.public_ip, instance.availability_zone).apply(
        lambda a: json.dumps({
            "vm": VM_NAME, "zone": a[2], "ip": a[1], "machine_type": INSTANCE_TYPE.replace(".", "-"),
            "provider": "aws", "instance_id": a[0], "region": "us-east-2",
        })
    ),
)
