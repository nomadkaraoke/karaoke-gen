"""
Artifact Registry for GPU Docker images.

Located in us-east4 (co-located with L4 GPU instances).
Stores the karaoke-backend-gpu base image used by the audio worker.
"""

import pulumi_gcp as gcp
from pulumi_gcp import artifactregistry

from config import PROJECT_ID

GPU_REGION = "us-east4"


def create_gpu_artifact_repo() -> artifactregistry.Repository:
    """Create Artifact Registry repo for GPU Docker images in us-east4."""
    return artifactregistry.Repository(
        "karaoke-backend-gpu-artifact-repo",
        repository_id="karaoke-backend-gpu",
        location=GPU_REGION,
        format="DOCKER",
        description="Docker repository for GPU-enabled karaoke-backend images (audio worker)",
    )
