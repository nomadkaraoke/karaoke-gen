"""
Networking resources for serverless VPC access.

Manages VPC connectors that allow Cloud Run to access internal VPC resources
like the flacfetch VM via its internal IP address.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import vpcaccess

from config import PROJECT_ID, REGION


def enable_vpc_access_api() -> gcp.projects.Service:
    """Enable the Serverless VPC Access API."""
    return gcp.projects.Service(
        "enable-vpcaccess-api",
        service="vpcaccess.googleapis.com",
        project=PROJECT_ID,
        disable_on_destroy=False,
    )


def create_vpc_connector(
    api_service: gcp.projects.Service,
) -> vpcaccess.Connector:
    """
    Create VPC connector for Cloud Run to access internal resources.

    Uses 10.8.0.0/28 which doesn't overlap with default subnet (10.128.0.0/20).
    This allows Cloud Run to reach VMs like flacfetch via their internal IPs,
    bypassing external firewall rules.

    Args:
        api_service: The VPC Access API service (ensures API is enabled first).

    Returns:
        vpcaccess.Connector: The VPC connector resource.
    """
    return vpcaccess.Connector(
        "cloud-run-vpc-connector",
        name="cloud-run-connector",
        region=REGION,
        network="default",
        ip_cidr_range="10.8.0.0/28",
        min_instances=2,
        max_instances=3,
        machine_type="e2-micro",
        opts=pulumi.ResourceOptions(depends_on=[api_service]),
    )
