"""
Artifact Registry for GPU Docker images.

Located in us-east4 (co-located with L4 GPU instances).
Stores the final GPU app image (karaoke-backend) pulled by the audio-separation
job and the CPU job image (karaoke-backend-cpu). The ~13 GB GPU *base* image
moved to us-central1 karaoke-repo on 2026-09-27.
"""

import pulumi_gcp as gcp
from pulumi_gcp import artifactregistry

from config import PROJECT_ID
from modules.artifact_registry import CLEANUP_POLICY_DRY_RUN, standard_cleanup_policies

GPU_REGION = "us-east4"


def create_gpu_artifact_repo() -> artifactregistry.Repository:
    """Create Artifact Registry repo for GPU Docker images in us-east4."""
    return artifactregistry.Repository(
        "karaoke-backend-gpu-artifact-repo",
        repository_id="karaoke-backend-gpu",
        location=GPU_REGION,
        format="DOCKER",
        description="Docker repository for GPU-enabled karaoke-backend images (audio worker)",
        # Cost control: GPU images are multi-GB. See standard_cleanup_policies().
        cleanup_policies=standard_cleanup_policies(),
        cleanup_policy_dry_run=CLEANUP_POLICY_DRY_RUN,
    )
