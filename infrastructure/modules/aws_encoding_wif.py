"""Workload Identity Federation for the AWS encoding worker.

The AWS encoding worker (EC2 in Nomad's AWS account) authenticates to GCP as
the existing ``encoding-worker`` service account with no keys: the instance's
IAM role ``nomad-encoding-worker`` is exchanged for a short-lived GCP token via
this pool/provider. The box's credential config is generated from the provider
name in infrastructure/encoding-worker/aws/provision.sh.
"""

import pulumi
import pulumi_gcp as gcp

from config import PROJECT_ID

AWS_ACCOUNT_ID = "790139457751"
AWS_WORKER_ROLE = "nomad-encoding-worker"
POOL_ID = "aws-encoding"
PROVIDER_ID = f"aws-{AWS_ACCOUNT_ID}"


def create_aws_encoding_wif(encoding_worker_sa: gcp.serviceaccount.Account) -> dict:
    pool = gcp.iam.WorkloadIdentityPool(
        "aws-encoding-wif-pool",
        workload_identity_pool_id=POOL_ID,
        display_name="AWS encoding worker",
        description="EC2 encoding worker in Nomad's AWS account (karaoke-gen)",
    )
    provider = gcp.iam.WorkloadIdentityPoolProvider(
        "aws-encoding-wif-provider",
        workload_identity_pool_id=pool.workload_identity_pool_id,
        workload_identity_pool_provider_id=PROVIDER_ID,
        display_name="Nomad AWS account",
        aws=gcp.iam.WorkloadIdentityPoolProviderAwsArgs(account_id=AWS_ACCOUNT_ID),
        attribute_mapping={
            "google.subject": "assertion.arn",
            "attribute.aws_role": (
                "assertion.arn.contains('assumed-role') ? "
                "assertion.arn.extract('{account_arn}assumed-role/') + 'assumed-role/' + "
                "assertion.arn.extract('assumed-role/{role_name}/') : assertion.arn"
            ),
        },
        # Only the worker's role in Nomad's account may federate.
        attribute_condition=(
            f"attribute.aws_role == 'arn:aws:sts::{AWS_ACCOUNT_ID}:assumed-role/{AWS_WORKER_ROLE}'"
        ),
    )
    binding = gcp.serviceaccount.IAMMember(
        "aws-encoding-worker-wif-user",
        service_account_id=encoding_worker_sa.name,
        role="roles/iam.workloadIdentityUser",
        member=pool.name.apply(
            lambda name: (
                f"principalSet://iam.googleapis.com/{name}/attribute.aws_role/"
                f"arn:aws:sts::{AWS_ACCOUNT_ID}:assumed-role/{AWS_WORKER_ROLE}"
            )
        ),
    )
    pulumi.export("aws_encoding_wif_provider", provider.name)
    return {"pool": pool, "provider": provider, "binding": binding}
